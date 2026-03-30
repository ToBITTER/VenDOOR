"""
Helpers for generating public-facing alphanumeric IDs.
"""

import secrets
import string

ALPHABET = string.ascii_uppercase + string.digits


def _random_token(length: int = 8) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def generate_seller_code() -> str:
    return f"SEL-{_random_token(8)}"


def generate_listing_code() -> str:
    return f"LST-{_random_token(8)}"
