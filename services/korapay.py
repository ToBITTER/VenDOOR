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


class KorapayClient:
    """
    Korapay API client for initiating and verifying payments.
    """
    
    def __init__(self):
        self.base_url = settings.korapay_base_url
        self.public_key = settings.korapay_public_key
        self.secret_key = settings.korapay_secret_key
        self.encryption_key = settings.korapay_encryption_key
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
            "notification_url": callback_url,  # Webhook for payment status
            "return_url": callback_url,        # Redirect after payment
            "currency": "NGN",
        }
        
        try:
            async with httpx.AsyncClient() as client:
                data = None

                # Attempt encrypted payload first when key is configured.
                if self.encryption_key:
                    try:
                        encrypted_payload = self.encrypt_payload(payload)
                        encrypted_response = await client.post(
                            f"{self.base_url}/charges/initialize",
                            json={"encrypted_data": encrypted_payload},
                            headers=self.headers,
                            timeout=10.0,
                        )
                        if encrypted_response.is_success:
                            data = encrypted_response.json()
                        else:
                            logger.warning(
                                "Korapay encrypted initialize failed with status %s; falling back to standard payload",
                                encrypted_response.status_code,
                            )
                    except Exception:
                        logger.exception("Failed encrypted Korapay initialize; falling back to standard payload")

                if data is None:
                    response = await client.post(
                        f"{self.base_url}/charges/initialize",
                        json=payload,
                        headers=self.headers,
                        timeout=10.0,
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
                payload_bytes = payload
            elif isinstance(payload, str):
                payload_bytes = payload.encode()
            else:
                payload_string = json.dumps(payload, separators=(",", ":"), sort_keys=True)
                payload_bytes = payload_string.encode()

            computed_signature = hmac.new(
                self.secret_key.encode(),
                payload_bytes,
                hashlib.sha256
            ).hexdigest()
            
            return hmac.compare_digest(computed_signature, signature)
        
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
