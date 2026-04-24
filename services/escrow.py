"""
Escrow service for managing payment holds and releases.
Handles state transitions for orders in escrow (PAID → COMPLETED → REFUNDED/RELEASED).
"""

from datetime import datetime, timedelta
from decimal import Decimal
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import Order, OrderStatus, SellerProfile

logger = logging.getLogger(__name__)


class EscrowService:
    """
    Service for managing escrow transactions.
    """
    
    SUCCESS_PAYOUT_STATUSES = {"success", "successful", "completed"}
    FAILED_PAYOUT_STATUSES = {"failed", "error", "cancelled", "reversed", "declined"}
    PROCESSING_PAYOUT_STATUSES = {"processing", "pending", "queued", "initiating", "initiated"}

    @staticmethod
    def _seller_payout_amount(order_amount: Decimal) -> Decimal:
        """
        Buyer amount includes 5% platform fee.
        Seller receives the base component.
        """
        return (Decimal(order_amount) / Decimal("1.05")).quantize(Decimal("0.01"))

    @staticmethod
    def _build_payout_reference(order_id: int) -> str:
        return f"VENDOOR_PAYOUT_{order_id}"

    @staticmethod
    def normalize_payout_status(raw_status: str | None) -> str:
        status = str(raw_status or "").strip().lower()
        if status == "not_authorized":
            return "not_authorized"
        if status in EscrowService.SUCCESS_PAYOUT_STATUSES:
            return "success"
        if status in EscrowService.FAILED_PAYOUT_STATUSES:
            return "failed"
        if status in EscrowService.PROCESSING_PAYOUT_STATUSES:
            return "processing"
        return status or "unknown"

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
            payout_reference = order.seller_payout_ref or EscrowService._build_payout_reference(order.id)
            order.seller_payout_ref = payout_reference
            order.seller_payout_attempted_at = datetime.utcnow()
            order.seller_payout_status = "initiating"
            await session.flush()

            from services.korapay import get_korapay_client

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
                normalized = EscrowService.normalize_payout_status(payout_result.status or "failed")
                order.seller_payout_status = normalized
                # Back off auto-release retries when merchant payout permissions are missing.
                if normalized == "not_authorized":
                    order.auto_release_scheduled_at = datetime.utcnow() + timedelta(hours=24)
                logger.error(
                    "Payout failed for order %s ref=%s status=%s message=%s",
                    order.id,
                    payout_reference,
                    payout_result.status,
                    payout_result.message,
                )
                await session.commit()
                return False

            order.status = OrderStatus.COMPLETED
            order.escrow_released_at = datetime.utcnow()
            order.auto_release_scheduled_at = None
            order.seller_payout_status = EscrowService.normalize_payout_status(payout_result.status or "processing")
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
    async def retry_failed_payout(order_id: int, session: AsyncSession) -> tuple[bool, str]:
        """
        Retry seller payout safely for a previously failed payout.
        Uses deterministic payout reference for idempotency.
        """
        try:
            result = await session.execute(
                select(Order)
                .options(selectinload(Order.seller).selectinload(SellerProfile.user))
                .where(Order.id == order_id)
                .with_for_update()
            )
            order = result.scalars().first()
            if not order:
                return False, "order_not_found"

            payout_reference = order.seller_payout_ref or EscrowService._build_payout_reference(order.id)
            order.seller_payout_ref = payout_reference
            normalized = EscrowService.normalize_payout_status(order.seller_payout_status)

            if normalized == "success":
                return False, "payout_already_successful"
            if normalized == "processing":
                return False, "payout_already_processing"
            if normalized == "not_authorized":
                return False, "korapay_payout_not_authorized"

            seller = order.seller
            if not seller or not seller.bank_code or not seller.account_number:
                return False, "seller_payout_details_missing"

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

            order.seller_payout_attempted_at = datetime.utcnow()
            order.seller_payout_status = "initiating"
            await session.flush()

            from services.korapay import get_korapay_client

            korapay = get_korapay_client()
            payout_result = await korapay.disburse_to_bank_account(
                reference=payout_reference,
                amount=payout_amount,
                bank_code=seller.bank_code,
                account_number=seller.account_number,
                customer_name=seller_name,
                customer_email=seller_email,
                narration=f"Order #{order.id} payout retry",
                metadata={"order_id": order.id, "seller_id": seller.id, "retry": True},
            )

            order.seller_payout_status = EscrowService.normalize_payout_status(
                payout_result.status if payout_result.ok else (payout_result.status or "failed")
            )
            await session.commit()
            if not payout_result.ok:
                return False, order.seller_payout_status or "failed"
            return True, order.seller_payout_status or "processing"
        except Exception:
            await session.rollback()
            logger.exception("Retry payout failed for order %s", order_id)
            return False, "exception"

    @staticmethod
    async def verify_payout_by_reference(reference: str, session: AsyncSession) -> dict:
        """
        Verify payout status with Korapay and persist on matching order row.
        """
        ref = str(reference or "").strip()
        if not ref:
            return {"ok": False, "error": "missing_reference"}

        result = await session.execute(
            select(Order).where(Order.seller_payout_ref == ref).with_for_update()
        )
        order = result.scalars().first()
        if not order:
            return {"ok": False, "error": "order_not_found_for_reference", "reference": ref}

        from services.korapay import get_korapay_client

        korapay = get_korapay_client()
        verification = await korapay.verify_payout(ref)
        normalized = EscrowService.normalize_payout_status(verification.status)
        if normalized != "unknown":
            order.seller_payout_status = normalized
        else:
            order.seller_payout_status = verification.status or order.seller_payout_status

        if order.seller_payout_status in {"success"}:
            if order.status == OrderStatus.PAID:
                order.status = OrderStatus.COMPLETED
            if order.escrow_released_at is None:
                order.escrow_released_at = datetime.utcnow()

        await session.commit()
        return {
            "ok": verification.ok,
            "reference": ref,
            "order_id": order.id,
            "payout_status": order.seller_payout_status,
            "message": verification.message,
        }
    
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

                # Funds movement reversal is provider-specific and should be
                # executed by admin workflow until API refund is integrated.
                logger.info("Escrow marked refunded for order %s", order_id)
                return True
            
            return False
        
        except Exception:
            await session.rollback()
            logger.exception("Escrow refund error for order %s", order_id)
            return False


# Singleton instance
_escrow_service = None


def get_escrow_service() -> EscrowService:
    """Get escrow service singleton."""
    global _escrow_service
    if _escrow_service is None:
        _escrow_service = EscrowService()
    return _escrow_service
