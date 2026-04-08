from decimal import Decimal

from services.escrow import EscrowService


def test_seller_payout_amount_removes_platform_fee():
    # Buyer pays +5%, seller receives base amount.
    assert EscrowService._seller_payout_amount(Decimal("1050.00")) == Decimal("1000.00")


def test_build_payout_reference_is_deterministic():
    assert EscrowService._build_payout_reference(42) == "VENDOOR_PAYOUT_42"
