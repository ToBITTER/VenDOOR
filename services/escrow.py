"""
Escrow service for managing payment holds and releases.
Handles state transitions for orders in escrow (PAID → COMPLETED → REFUNDED/RELEASED).
"""

from datetime import datetime
from decimal import Decimal
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import Order, OrderStatus, SellerProfile
from services.korapay import get_korapay_client

logger = logging.getLogger(__name__)


class EscrowService:
    """
    Service for managing escrow transactions.
    """
    
    @staticmethod
    def _seller_payout_amount(order_amount: Decimal) -> Decimal:
        """
        Buyer amount includes 5% platform fee.
        Seller receives the base component.
        """
        return (Decimal(order_amount) / Decimal("1.05")).quantize(Decimal("0.01"))

    @staticmethod
    async def release_escrow(order_id: int, session: AsyncSession) -> bool:
        """
        Release escrow funds to seller after order completion.
        
        Args:
            order_id: Order ID to release funds for
            session: Database session
        
        Returns:
            True if released successfully, False otherwise
        """
        try:
            result = await session.execute(
                select(Order)
                .options(selectinload(Order.seller).selectinload(SellerProfile.user))
                .where(Order.id == order_id)
            )
            order = result.scalars().first()
            
            if not order:
                logger.warning("Order %s not found for escrow release", order_id)
                return False

            if order.escrow_released_at:
                logger.info("Escrow already released for order %s", order_id)
                return True

            if order.status != OrderStatus.PAID:
                logger.warning(
                    "Cannot release escrow for order %s with status %s",
                    order_id,
                    order.status,
                )
                return False

            seller = order.seller
            if not seller:
                logger.error("Order %s has no seller profile", order_id)
                return False
            if not seller.bank_code or not seller.account_number:
                logger.error("Seller payout details missing for order %s seller_id=%s", order_id, seller.id)
                return False

            seller_name = (
                seller.account_name
                or seller.full_name
                or (seller.user.first_name if seller.user else None)
                or "Seller"
            )
            seller_email = (
                seller.student_email
                or (f"{seller.user.username}@telegram.local" if seller.user and seller.user.username else None)
                or f"seller{seller.id}@vendoor.local"
            )
            payout_amount = EscrowService._seller_payout_amount(order.amount)
            payout_reference = f"VENDOOR_PAYOUT_{order.id}"

            korapay = get_korapay_client()
            payout_result = await korapay.disburse_to_bank_account(
                reference=payout_reference,
                amount=payout_amount,
                bank_code=seller.bank_code,
                account_number=seller.account_number,
                customer_name=seller_name,
                customer_email=seller_email,
                narration=f"Order #{order.id} payout",
                metadata={"order_id": order.id, "seller_id": seller.id},
            )

            if not payout_result.ok:
                logger.error(
                    "Payout failed for order %s ref=%s status=%s message=%s",
                    order.id,
                    payout_reference,
                    payout_result.status,
                    payout_result.message,
                )
                await session.rollback()
                return False

            order.status = OrderStatus.COMPLETED
            order.escrow_released_at = datetime.utcnow()
            order.auto_release_scheduled_at = None
            await session.commit()

            logger.info(
                "Escrow released and payout initiated for order %s payout_ref=%s amount=%s",
                order_id,
                payout_result.reference,
                payout_amount,
            )
            return True
        
        except Exception as e:
            await session.rollback()
            logger.exception("Escrow release error for order %s", order_id)
            return False
    
    @staticmethod
    async def auto_release_escrow(order_id: int, session: AsyncSession) -> bool:
        """
        Automatically release escrow after 48 hours if not disputed.
        
        Args:
            order_id: Order ID to auto-release
            session: Database session
        
        Returns:
            True if auto-released, False otherwise
        """
        try:
            result = await session.execute(select(Order).where(Order.id == order_id))
            order = result.scalars().first()
            if not order:
                return False
            if order.status != OrderStatus.PAID:
                return False
            return await EscrowService.release_escrow(order_id, session)
        except Exception:
            await session.rollback()
            logger.exception("Auto-release escrow error for order %s", order_id)
            return False
    
    @staticmethod
    async def refund_escrow(order_id: int, session: AsyncSession) -> bool:
        """
        Refund escrow funds back to buyer (in case of dispute resolution).
        
        Args:
            order_id: Order ID to refund
            session: Database session
        
        Returns:
            True if refunded, False otherwise
        """
        try:
            result = await session.execute(
                select(Order).where(Order.id == order_id)
            )
            order = result.scalars().first()
            
            if not order:
                return False
            
            # Only refund if in PAID or DISPUTED status
            if order.status in (OrderStatus.PAID, OrderStatus.DISPUTED):
                order.status = OrderStatus.REFUNDED
                await session.commit()
                
                # TODO: Initiate refund via Korapay
                # await initiate_refund(order)
                
                print(f"Refunded escrow for order {order_id}")
                return True
            
            return False
        
        except Exception as e:
            await session.rollback()
            print(f"Escrow refund error: {e}")
            return False


# Singleton instance
_escrow_service = None


def get_escrow_service() -> EscrowService:
    """Get escrow service singleton."""
    global _escrow_service
    if _escrow_service is None:
        _escrow_service = EscrowService()
    return _escrow_service
