"""
Korapay API client for handling payment operations.
All API calls use httpx (async HTTP client).
"""

from decimal import Decimal
from typing import Optional
import httpx
import hmac
import hashlib
import json
import logging
import os
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from core.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)


@dataclass
class KorapayReference:
    """Response from initializing a Korapay payment."""
    checkout_url: str
    reference: str
    amount: Decimal


@dataclass
class KorapayPayoutResult:
    """Response from creating a Korapay payout/disbursement."""

    ok: bool
    reference: str
    status: str | None = None
    message: str | None = None
    raw: dict | None = None


class KorapayClient:
    """
    Korapay API client for initiating and verifying payments.
    """
    ALLOWED_CHANNELS = {
        "card",
        "bank_transfer",
        "pay_with_bank",
        "mobile_money",
        "voucher",
    }
    
    def __init__(self):
        self.base_url = settings.korapay_base_url
        self.public_key = settings.korapay_public_key
        self.secret_key = settings.korapay_secret_key
        self.encryption_key = settings.korapay_encryption_key
        self.payment_channels = settings.korapay_payment_channels
        self.headers = {
            "Authorization": f"Bearer {self.secret_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _is_success_status(value) -> bool:
        if value is True:
            return True
        if isinstance(value, str):
            return value.lower() == "success"
        return False

    def _resolve_encryption_key(self) -> bytes:
        key = (self.encryption_key or "").strip()
        if not key:
            raise ValueError("Korapay encryption key is not configured")

        if key.startswith("0x"):
            key = key[2:]

        # Accept both plain-text 32-char keys and hex-encoded 32-byte keys.
        try:
            maybe_hex = bytes.fromhex(key)
            if len(maybe_hex) == 32:
                return maybe_hex
        except ValueError:
            pass

        raw = key.encode("utf-8")
        if len(raw) != 32:
            raise ValueError("Korapay encryption key must be 32 bytes for AES-256-GCM")
        return raw

    def encrypt_payload(self, payload: dict) -> str:
        """
        Encrypt payload for Korapay endpoints that accept encrypted request bodies.
        Output format: iv:ciphertext:auth_tag (all hex encoded).
        """
        key = self._resolve_encryption_key()
        iv = os.urandom(16)
        aesgcm = AESGCM(key)
        plaintext = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        encrypted = aesgcm.encrypt(iv, plaintext, None)
        ciphertext, tag = encrypted[:-16], encrypted[-16:]
        return f"{iv.hex()}:{ciphertext.hex()}:{tag.hex()}"

    def _parsed_channels(self) -> list[str]:
        raw = (self.payment_channels or "").strip()
        if not raw:
            return []
        seen: set[str] = set()
        valid: list[str] = []
        invalid: list[str] = []

        for channel in (part.strip().lower() for part in raw.split(",")):
            if not channel:
                continue
            if channel in seen:
                continue
            seen.add(channel)
            if channel in self.ALLOWED_CHANNELS:
                valid.append(channel)
            else:
                invalid.append(channel)

        if invalid:
            logger.warning(
                "Ignoring invalid Korapay payment channels: %s. Allowed: %s",
                ",".join(invalid),
                ",".join(sorted(self.ALLOWED_CHANNELS)),
            )
        return valid
    
    async def initialize_charge(
        self,
        amount: Decimal,
        reference: str,
        customer_email: str,
        customer_name: str,
        callback_url: str,
    ) -> Optional[KorapayReference]:
        """
        Initialize a charge with Korapay.
        
        Args:
            amount: Amount in Naira (e.g., 5000.00)
            reference: Unique transaction reference
            customer_email: Customer email
            customer_name: Customer name
            callback_url: URL to return to after payment
        
        Returns:
            KorapayReference with checkout URL, or None if failed
        """
        payload = {
            "amount": float(amount),
            "reference": reference,
            "customer": {
                "email": customer_email,
                "name": customer_name,
            },
            "metadata": {"order_reference": reference},
            "notification_url": callback_url,  # Webhook for payment status
            "currency": "NGN",
        }
        channels = self._parsed_channels()
        if channels:
            payload["channels"] = channels
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/charges/initialize",
                    json=payload,
                    headers=self.headers,
                    timeout=10.0,
                )
                if not response.is_success:
                    logger.error(
                        "Korapay initialize failed status=%s response=%s payload_keys=%s",
                        response.status_code,
                        response.text[:1200],
                        list(payload.keys()),
                    )
                response.raise_for_status()
                data = response.json()

                if self._is_success_status(data.get("status")):
                    checkout_link = data.get("data", {}).get("checkout_url")
                    if checkout_link:
                        return KorapayReference(
                            checkout_url=checkout_link,
                            reference=reference,
                            amount=amount,
                        )
        
        except httpx.HTTPError as e:
            logger.exception("Korapay initialization error")
        
        return None
    
    async def verify_charge(self, reference: str) -> Optional[dict]:
        """
        Verify a charge with Korapay.
        
        Args:
            reference: Transaction reference returned from initialize_charge
        
        Returns:
            Payment details dict with status, amount, etc., or None if failed
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/charges/{reference}/verify",
                    headers=self.headers,
                    timeout=10.0,
                )
                response.raise_for_status()
                
                data = response.json()
                
                if self._is_success_status(data.get("status")):
                    return data.get("data")
        
        except httpx.HTTPError as e:
            logger.exception("Korapay verification error")
        
        return None

    async def disburse_to_bank_account(
        self,
        *,
        reference: str,
        amount: Decimal,
        bank_code: str,
        account_number: str,
        customer_name: str,
        customer_email: str,
        narration: str | None = None,
        currency: str = "NGN",
        metadata: dict | None = None,
    ) -> KorapayPayoutResult:
        """
        Initiate payout/disbursement to a bank account.
        Korapay docs: POST /merchant/api/v1/transactions/disburse
        """
        payload = {
            "reference": reference,
            "destination": {
                "type": "bank_account",
                "amount": float(amount),
                "currency": currency,
                "narration": narration or f"Escrow payout for {reference}",
                "bank_account": {
                    "bank": str(bank_code).strip(),
                    "account": str(account_number).strip(),
                },
                "customer": {
                    "name": (customer_name or "Seller").strip(),
                    "email": (customer_email or "seller@vendoor.local").strip(),
                },
            },
        }
        if metadata:
            payload["metadata"] = metadata

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/transactions/disburse",
                    json=payload,
                    headers=self.headers,
                    timeout=15.0,
                )
                data = response.json()
                status_value = str(data.get("status")).lower() if isinstance(data, dict) else None
                is_ok = self._is_success_status(data.get("status")) if isinstance(data, dict) else False

                if not response.is_success or not is_ok:
                    logger.error(
                        "Korapay disbursement failed status_code=%s response=%s reference=%s",
                        response.status_code,
                        response.text[:1200],
                        reference,
                    )
                    return KorapayPayoutResult(
                        ok=False,
                        reference=reference,
                        status=status_value,
                        message=(data.get("message") if isinstance(data, dict) else "payout_failed"),
                        raw=data if isinstance(data, dict) else None,
                    )

                payout_data = data.get("data") if isinstance(data, dict) else {}
                payout_status = str((payout_data or {}).get("status") or "").strip().lower() or None
                return KorapayPayoutResult(
                    ok=True,
                    reference=str((payout_data or {}).get("reference") or reference),
                    status=payout_status,
                    message=data.get("message") if isinstance(data, dict) else None,
                    raw=data if isinstance(data, dict) else None,
                )
        except Exception:
            logger.exception("Korapay disbursement exception for reference=%s", reference)
            return KorapayPayoutResult(ok=False, reference=reference, status="error", message="exception")
    
    def verify_webhook_signature(self, payload: dict | str | bytes, signature: str) -> bool:
        """
        Verify that a webhook came from Korapay.
        
        Args:
            payload: Raw webhook payload
            signature: X-Korapay-Signature header value
        
        Returns:
            True if signature is valid, False otherwise
        """
        try:
            if isinstance(payload, bytes):
                payload = json.loads(payload.decode("utf-8"))
            elif isinstance(payload, str):
                payload = json.loads(payload)

            if isinstance(payload, dict):
                # Korapay signs ONLY the `data` object.
                signed_obj = payload.get("data", {})
            else:
                signed_obj = {}

            serialized_candidates = [
                json.dumps(signed_obj, separators=(",", ":"), ensure_ascii=False).encode(),
                json.dumps(signed_obj, ensure_ascii=False).encode(),
                json.dumps(signed_obj, separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode(),
            ]

            for payload_bytes in serialized_candidates:
                computed_signature = hmac.new(
                    self.secret_key.encode(),
                    payload_bytes,
                    hashlib.sha256,
                ).hexdigest()
                if hmac.compare_digest(computed_signature, signature):
                    return True
            return False
        
        except Exception:
            logger.exception("Webhook signature verification error")
            return False


# Singleton instance
_korapay_client = None


def get_korapay_client() -> KorapayClient:
    """Get or create Korapay client singleton."""
    global _korapay_client
    if _korapay_client is None:
        _korapay_client = KorapayClient()
    return _korapay_client
