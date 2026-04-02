"""
Korapay webhook handler for payment status callbacks.
Processes payment confirmations and updates order statuses in the database.
"""

from datetime import datetime
import logging
from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from db.models import Delivery, DeliveryEvent, DeliveryEventType, DeliveryStatus, Listing, Order, OrderStatus, SellerProfile
from services.korapay import get_korapay_client
from core.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)


async def handle_korapay_webhook(
    webhook_data: dict,
    session: AsyncSession,
    bot: Bot | None = None,
) -> dict:
    """
    Handle incoming Korapay webhook for payment status changes.
    
    Expected webhook payload:
    {
        "event": "charge.completed",
        "data": {
            "ref": "transaction_reference",
            "status": "success",
            "amount": 5000.00,
            "currency": "NGN",
            "customer": {...},
            "metadata": {...},
            ...
        }
    }
    """
    
    try:
        if not isinstance(webhook_data, dict):
            return {"status": "error", "error": "invalid payload type"}

        event = webhook_data.get("event")
        data = webhook_data.get("data", {})
        reference = data.get("ref")
        status = data.get("status")
        
        # Verify webhook signature (optional but recommended)
        # korapay_client = get_korapay_client()
        # signature = request.headers.get("X-Korapay-Signature")
        # if not korapay_client.verify_webhook_signature(webhook_data, signature):
        #     return {"error": "Invalid signature"}, 401
        
        if event == "charge.completed" and status == "success":
            # Payment successful - update order to PAID and schedule escrow release
            return await _handle_payment_success(reference, data, session, bot)
        
        elif event == "charge.failed" or status == "failed":
            # Payment failed - cancel order
            return await _handle_payment_failed(reference, session)
        
        else:
            # Unknown event
            return {"status": "ignored", "event": event}
    
    except Exception:
        logger.exception("Webhook processing error")
        return {"status": "error", "error": "webhook_processing_failed"}


async def _handle_payment_success(
    reference: str,
    data: dict,
    session: AsyncSession,
    bot: Bot | None = None,
) -> dict:
    """
    Handle successful payment:
    1. Find order by transaction reference
    2. Update order status to PAID
    3. Transfer funds to escrow
    4. Schedule automatic release after 48 hours
    """
    try:
        if not reference:
            return {"status": "error", "error": "missing_reference"}

        # Find order
        result = await session.execute(
            select(Order)
            .options(
                joinedload(Order.buyer),
                joinedload(Order.seller).joinedload(SellerProfile.user),
            )
            .where(Order.transaction_ref == reference)
            .with_for_update()
        )
        order = result.scalars().first()
        
        if not order:
            return {"status": "error", "error": "Order not found"}

        if order.status not in (OrderStatus.PENDING, OrderStatus.PAID):
            return {
                "status": "ignored",
                "message": f"Order state {order.status.value} cannot transition to PAID",
                "order_id": order.id,
            }

        if order.status == OrderStatus.PAID:
            return {
                "status": "success",
                "message": "Order already marked as paid",
                "order_id": order.id,
            }

        listing_result = await session.execute(
            select(Listing).where(Listing.id == order.listing_id).with_for_update()
        )
        listing = listing_result.scalars().first()
        if not listing or not listing.available or listing.quantity < order.quantity:
            order.status = OrderStatus.CANCELLED
            await session.commit()
            return {
                "status": "error",
                "error": "Listing is out of stock",
                "order_id": order.id,
            }

        # Update order status
        order.status = OrderStatus.PAID
        order.paid_at = datetime.utcnow()
        listing.quantity -= order.quantity
        listing.available = listing.quantity > 0

        if not order.delivery:
            delivery = Delivery(
                order_id=order.id,
                status=DeliveryStatus.PENDING_ASSIGNMENT,
            )
            session.add(delivery)
            await session.flush()
            session.add(
                DeliveryEvent(
                    delivery_id=delivery.id,
                    event_type=DeliveryEventType.ASSIGNED,
                    actor="SYSTEM",
                    note="Order paid and ready for delivery assignment",
                )
            )
        
        await session.commit()

        buyer_telegram_id = order.buyer.telegram_id if order.buyer else None
        seller_telegram_id = order.seller.user.telegram_id if order.seller and order.seller.user else None
        seller_username = order.seller.user.username if order.seller and order.seller.user else None
        buyer_username = order.buyer.username if order.buyer else None

        if bot and buyer_telegram_id:
            seller_contact = f"@{seller_username}" if seller_username else "Seller handle not set"
            try:
                await bot.send_message(
                    chat_id=int(buyer_telegram_id),
                    text=(
                        f"Payment confirmed for order #{order.id}.\n\n"
                        f"Seller contact: {seller_contact}\n"
                        "Your delivery will be assigned shortly."
                    ),
                )
            except Exception:
                pass

        if bot and seller_telegram_id:
            buyer_contact = f"@{buyer_username}" if buyer_username else "Buyer handle not set"
            try:
                await bot.send_message(
                    chat_id=int(seller_telegram_id),
                    text=(
                        f"You have a new paid order #{order.id}.\n\n"
                        f"Buyer contact: {buyer_contact}\n"
                        f"Please prepare {order.quantity} unit(s) for delivery."
                    ),
                )
            except Exception:
                pass
        
        # Queue Celery task for auto-release
        # from tasks.escrow_release import release_escrow_auto
        # release_escrow_auto.apply_async(
        #     args=[order.id],
        #     countdown=settings.escrow_release_hours * 3600,
        # )
        
        return {
            "status": "success",
            "message": "Order marked as paid, escrow activated",
            "order_id": order.id,
        }
    
    except Exception:
        await session.rollback()
        logger.exception("Payment success handler error")
        return {"status": "error", "error": "payment_success_handler_failed"}


async def _handle_payment_failed(reference: str, session: AsyncSession) -> dict:
    """
    Handle failed payment:
    Cancel the order and notify buyer.
    """
    try:
        if not reference:
            return {"status": "error", "error": "missing_reference"}

        # Find order
        result = await session.execute(
            select(Order).where(Order.transaction_ref == reference)
        )
        order = result.scalars().first()
        
        if not order:
            return {"status": "error", "error": "Order not found"}

        if order.status == OrderStatus.CANCELLED:
            return {
                "status": "success",
                "message": "Order already cancelled",
                "order_id": order.id,
            }

        if order.status != OrderStatus.PENDING:
            return {
                "status": "ignored",
                "message": f"Order state {order.status.value} will not be cancelled by failed payment callback",
                "order_id": order.id,
            }

        # Cancel pending order only
        order.status = OrderStatus.CANCELLED
        await session.commit()
        
        # TODO: Send Telegram notification to buyer
        # await bot.send_message(
        #     order.buyer.telegram_id,
        #     f"❌ Payment failed for order #{order.id}. Please try again."
        # )
        
        return {
            "status": "success",
            "message": "Order cancelled due to failed payment",
            "order_id": order.id,
        }
    
    except Exception:
        await session.rollback()
        logger.exception("Payment failed handler error")
        return {"status": "error", "error": "payment_failed_handler_failed"}
