"""
Korapay webhook handler for payment status callbacks.
Processes payment confirmations and updates order statuses in the database.
"""

from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Order, OrderStatus
from services.korapay import get_korapay_client
from core.config import get_settings

settings = get_settings()


async def handle_korapay_webhook(webhook_data: dict, session: AsyncSession) -> dict:
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
            return await _handle_payment_success(reference, data, session)
        
        elif event == "charge.failed" or status == "failed":
            # Payment failed - cancel order
            return await _handle_payment_failed(reference, session)
        
        else:
            # Unknown event
            return {"status": "ignored", "event": event}
    
    except Exception as e:
        print(f"Webhook processing error: {e}")
        return {"status": "error", "error": str(e)}


async def _handle_payment_success(reference: str, data: dict, session: AsyncSession) -> dict:
    """
    Handle successful payment:
    1. Find order by transaction reference
    2. Update order status to PAID
    3. Transfer funds to escrow
    4. Schedule automatic release after 48 hours
    """
    try:
        # Find order
        result = await session.execute(
            select(Order).where(Order.transaction_ref == reference)
        )
        order = result.scalars().first()
        
        if not order:
            return {"status": "error", "error": "Order not found"}
        
        # Update order status
        order.status = OrderStatus.PAID
        order.paid_at = datetime.utcnow()
        
        # Schedule automatic escrow release
        # (Celery task triggers after 48 hours)
        from core.config import get_settings
        settings = get_settings()
        order.auto_release_scheduled_at = datetime.utcnow() + timedelta(
            hours=settings.escrow_release_hours
        )
        
        await session.commit()
        
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
    
    except Exception as e:
        await session.rollback()
        print(f"Payment success handler error: {e}")
        return {"status": "error", "error": str(e)}


async def _handle_payment_failed(reference: str, session: AsyncSession) -> dict:
    """
    Handle failed payment:
    Cancel the order and notify buyer.
    """
    try:
        # Find order
        result = await session.execute(
            select(Order).where(Order.transaction_ref == reference)
        )
        order = result.scalars().first()
        
        if not order:
            return {"status": "error", "error": "Order not found"}
        
        # Cancel order
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
    
    except Exception as e:
        await session.rollback()
        print(f"Payment failed handler error: {e}")
        return {"status": "error", "error": str(e)}
