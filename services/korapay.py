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
from dataclasses import dataclass

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
        self.headers = {
            "Authorization": f"Bearer {self.secret_key}",
            "Content-Type": "application/json",
        }
    
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
                response = await client.post(
                    f"{self.base_url}/charges/initialize",
                    json=payload,
                    headers=self.headers,
                    timeout=10.0,
                )
                response.raise_for_status()
                
                data = response.json()
                
                if data.get("status") == "success":
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
                
                if data.get("status") == "success":
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
