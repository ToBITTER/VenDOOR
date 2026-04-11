"""
Korapay webhook handler for payment status callbacks.
Processes payment confirmations and updates order statuses in the database.
"""

from datetime import datetime
import hashlib
import json
import logging
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import (
    AdminUser,
    Delivery,
    DeliveryEvent,
    DeliveryEventType,
    DeliveryOrder,
    DeliveryStatus,
    Listing,
    Order,
    OrderStatus,
    SellerProfile,
    WebhookReceipt,
)
from services.korapay import get_korapay_client
from core.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)


async def _notify_admins_new_paid_order(
    session: AsyncSession,
    bot: Bot | None,
    order: Order,
    delivery_id: int | None,
) -> None:
    if not bot:
        return

    recipients: set[int] = set()
    if settings.admin_telegram_id and str(settings.admin_telegram_id).isdigit():
        recipients.add(int(settings.admin_telegram_id))

    admins_result = await session.execute(select(AdminUser.telegram_id))
    for telegram_id in admins_result.scalars().all():
        if telegram_id and str(telegram_id).isdigit():
            recipients.add(int(telegram_id))

    if not recipients:
        return

    item_title = order.listing.title if order.listing else "Unknown item"
    text = (
        "<b>New Paid Order</b>\n\n"
        f"<b>Order ID:</b> #{order.id}\n"
        f"<b>Item:</b> {order.quantity} x {item_title}\n"
        f"<b>Amount:</b> NGN {order.amount:,.2f}\n"
        f"<b>Delivery To:</b> {order.buyer_address or 'N/A'}\n"
    )
    if delivery_id is not None:
        text += f"<b>Delivery ID:</b> #{delivery_id}\n"

    keyboard = None
    if delivery_id is not None:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=f"Assign Order #{order.id}", callback_data=f"admin_delivery_assign_{delivery_id}")]
            ]
        )

    for chat_id in recipients:
        try:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=keyboard)
        except Exception:
            logger.exception("Failed admin new-order alert order_id=%s chat_id=%s", order.id, chat_id)


async def _notify_admins_new_paid_delivery(
    session: AsyncSession,
    bot: Bot | None,
    delivery_id: int,
    orders: list[Order],
) -> None:
    if not bot or not orders:
        return

    recipients: set[int] = set()
    if settings.admin_telegram_id and str(settings.admin_telegram_id).isdigit():
        recipients.add(int(settings.admin_telegram_id))

    admins_result = await session.execute(select(AdminUser.telegram_id))
    for telegram_id in admins_result.scalars().all():
        if telegram_id and str(telegram_id).isdigit():
            recipients.add(int(telegram_id))

    if not recipients:
        return

    total_amount = sum(order.amount for order in orders)
    total_items = sum(order.quantity for order in orders)
    order_ids_text = ", ".join(f"#{order.id}" for order in orders)
    lines = [
        "<b>New Combined Paid Order</b>",
        "",
        f"<b>Delivery ID:</b> #{delivery_id}",
        f"<b>Order IDs:</b> {order_ids_text}",
        f"<b>Total Items:</b> {total_items}",
        f"<b>Total Amount:</b> NGN {total_amount:,.2f}",
        "",
        "<b>Items:</b>",
    ]
    for order in orders:
        item_title = order.listing.title if order.listing else "Unknown item"
        lines.append(f"#{order.id} • {order.quantity} x {item_title}")

    text = "\n".join(lines)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"Assign Order #{orders[0].id}", callback_data=f"admin_delivery_assign_{delivery_id}")]
        ]
    )

    for chat_id in recipients:
        try:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=keyboard)
        except Exception:
            logger.exception("Failed admin combined-order alert delivery_id=%s chat_id=%s", delivery_id, chat_id)


async def _create_webhook_receipt(
    session: AsyncSession,
    provider: str,
    event_type: str | None,
    reference: str | None,
    payload: dict,
) -> bool:
    """
    Returns True when this webhook payload is new, False when already processed.
    """
    payload_hash = hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    session.add(
        WebhookReceipt(
            provider=provider,
            event_type=event_type,
            reference=reference,
            payload_hash=payload_hash,
        )
    )
    try:
        await session.flush()
        return True
    except IntegrityError:
        await session.rollback()
        return False


async def _ensure_delivery_order_link(
    session: AsyncSession,
    delivery_id: int,
    order_id: int,
    sequence: int = 1,
) -> None:
    existing = await session.execute(
        select(DeliveryOrder).where(
            DeliveryOrder.delivery_id == delivery_id,
            DeliveryOrder.order_id == order_id,
        )
    )
    if existing.scalars().first():
        return

    session.add(
        DeliveryOrder(
            delivery_id=delivery_id,
            order_id=order_id,
            sequence=sequence,
        )
    )


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

        event = str(webhook_data.get("event") or "").strip()
        data = webhook_data.get("data", {})
        reference = _extract_reference(data)
        status = str(data.get("status") or "").strip().lower()
        event_lc = event.lower()

        logger.info(
            "Korapay webhook received event=%s status=%s reference=%s keys=%s",
            event or "N/A",
            status or "N/A",
            reference or "N/A",
            ",".join(sorted(data.keys())) if isinstance(data, dict) else "N/A",
        )

        # Idempotency guard: ignore duplicate webhook events for same event/reference.
        is_new = await _create_webhook_receipt(
            session=session,
            provider="KORAPAY",
            event_type=event,
            reference=reference,
            payload=webhook_data,
        )
        if not is_new:
            return {"status": "ignored", "event": event, "reason": "duplicate_webhook"}
        
        # Verify webhook signature (optional but recommended)
        # korapay_client = get_korapay_client()
        # signature = request.headers.get("X-Korapay-Signature")
        # if not korapay_client.verify_webhook_signature(webhook_data, signature):
        #     return {"error": "Invalid signature"}, 401
        
        success_events = {"charge.completed", "charge.success", "charge.successful"}
        failed_events = {"charge.failed", "charge.cancelled"}
        success_statuses = {"success", "successful", "paid"}
        failed_statuses = {"failed", "cancelled"}

        if event_lc in success_events or status in success_statuses:
            # Payment successful - update order to PAID and schedule escrow release
            return await _handle_payment_success(reference, data, session, bot)

        elif event_lc in failed_events or status in failed_statuses:
            # Payment failed - cancel order
            return await _handle_payment_failed(reference, session)

        else:
            # Unknown event
            logger.warning(
                "Korapay webhook ignored event=%s status=%s reference=%s",
                event or "N/A",
                status or "N/A",
                reference or "N/A",
            )
            return {"status": "ignored", "event": event}
    
    except Exception:
        logger.exception("Webhook processing error")
        return {"status": "error", "error": "webhook_processing_failed"}


def _extract_reference(data: dict) -> str | None:
    """
    Korapay payloads may vary by event/version. Try common reference keys.
    """
    if not isinstance(data, dict):
        return None

    candidates = [
        data.get("ref"),
        data.get("reference"),
        data.get("merchant_reference"),
        data.get("transaction_reference"),
        data.get("tx_ref"),
    ]

    metadata = data.get("metadata")
    if isinstance(metadata, dict):
        candidates.extend(
            [
                metadata.get("order_reference"),
                metadata.get("reference"),
                metadata.get("ref"),
            ]
        )

    for candidate in candidates:
        text = str(candidate or "").strip()
        if text:
            return text
    return None


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

        if reference.startswith("VENDOOR_CART_"):
            return await _handle_cart_payment_success(reference, session, bot)

        # Find order
        result = await session.execute(
            select(Order)
            .options(
                # Keep row-lock query free of JOIN eager-loads (Postgres FOR UPDATE + outer joins can fail).
                selectinload(Order.buyer),
                selectinload(Order.seller).selectinload(SellerProfile.user),
                selectinload(Order.listing),
                selectinload(Order.delivery),
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
            await _ensure_delivery_order_link(session, delivery.id, order.id)
            session.add(
                DeliveryEvent(
                    delivery_id=delivery.id,
                    event_type=DeliveryEventType.ASSIGNED,
                    actor="SYSTEM",
                    note="Order paid and ready for delivery assignment",
                )
            )
        
        await session.commit()

        delivery_id = order.delivery.id if order.delivery else None

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
                        "Payment received successfully.\n\n"
                        f"Order: #{order.id}\n"
                        f"Seller contact: {seller_contact}\n\n"
                        "Next step: we assign an agent and send you tracking."
                    ),
                )
            except Exception:
                logger.exception("Failed to notify buyer for paid order %s", order.id)

        if bot and seller_telegram_id:
            buyer_contact = f"@{buyer_username}" if buyer_username else "Buyer handle not set"
            try:
                seller_keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text=f"View Order #{order.id}", callback_data=f"seller_view_order_{order.id}")],
                        [InlineKeyboardButton(text="My Listings", callback_data="seller_listings")],
                    ]
                )
                await bot.send_message(
                    chat_id=int(seller_telegram_id),
                    text=(
                        f"You have a new paid order #{order.id}.\n\n"
                        f"Buyer contact: {buyer_contact}\n"
                        f"Item: {order.quantity} x {order.listing.title if order.listing else 'Unknown item'}\n"
                        f"Delivery To: {order.buyer_address or 'N/A'}\n\n"
                        f"Please prepare {order.quantity} unit(s) now."
                    ),
                    reply_markup=seller_keyboard,
                )
            except Exception:
                logger.exception("Failed to notify seller for paid order %s", order.id)

        await _notify_admins_new_paid_order(session, bot, order, delivery_id)
        
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


def _extract_cart_reference_order_ids(reference: str) -> list[int]:
    """
    Expected format:
    VENDOOR_CART_<buyer_telegram_id>_<timestamp>_<orderid-orderid-...>
    """
    try:
        parts = reference.split("_", 4)
        if len(parts) != 5:
            return []
        order_ids_part = parts[4]
        order_ids = []
        for token in order_ids_part.split("-"):
            if token.isdigit():
                order_ids.append(int(token))
        return order_ids
    except Exception:
        return []


async def _handle_cart_payment_success(
    reference: str,
    session: AsyncSession,
    bot: Bot | None = None,
) -> dict:
    order_ids = _extract_cart_reference_order_ids(reference)
    if not order_ids:
        return {"status": "error", "error": "invalid_cart_reference"}

    result = await session.execute(
        select(Order)
        .options(
            # Use selectinload so FOR UPDATE only locks orders rows and avoids outer-join lock errors.
            selectinload(Order.buyer),
            selectinload(Order.seller).selectinload(SellerProfile.user),
            selectinload(Order.listing),
            selectinload(Order.delivery),
        )
        .where(Order.id.in_(order_ids))
        .with_for_update()
    )
    orders = result.scalars().all()
    if not orders:
        return {"status": "error", "error": "orders_not_found_for_cart_reference"}

    pending_orders = [order for order in orders if order.status == OrderStatus.PENDING]
    if not pending_orders:
        return {
            "status": "success",
            "message": "Cart orders already marked as paid",
            "order_ids": [order.id for order in orders],
        }

    # Validate stock before committing status updates.
    required_by_listing: dict[int, int] = {}
    for order in pending_orders:
        required_by_listing[order.listing_id] = required_by_listing.get(order.listing_id, 0) + order.quantity

    listing_rows = await session.execute(
        select(Listing).where(Listing.id.in_(list(required_by_listing.keys()))).with_for_update()
    )
    listings_by_id = {listing.id: listing for listing in listing_rows.scalars().all()}

    for listing_id, required_qty in required_by_listing.items():
        listing = listings_by_id.get(listing_id)
        if not listing or not listing.available or listing.quantity < required_qty:
            for order in pending_orders:
                order.status = OrderStatus.CANCELLED
            await session.commit()
            return {
                "status": "error",
                "error": "listing_out_of_stock_for_cart_payment",
                "listing_id": listing_id,
            }

    now = datetime.utcnow()
    for listing_id, required_qty in required_by_listing.items():
        listing = listings_by_id[listing_id]
        listing.quantity -= required_qty
        listing.available = listing.quantity > 0

    shared_delivery: Delivery | None = None
    for idx, order in enumerate(pending_orders, start=1):
        order.status = OrderStatus.PAID
        order.paid_at = now
        if order.transaction_ref is None:
            synthesized_ref = f"{reference}_{order.id}"
            order.transaction_ref = synthesized_ref[:255]

        # Create one shared delivery job for all cart orders.
        if shared_delivery is None:
            if order.delivery:
                shared_delivery = order.delivery
            else:
                shared_delivery = Delivery(order_id=order.id, status=DeliveryStatus.PENDING_ASSIGNMENT)
                session.add(shared_delivery)
                await session.flush()
                session.add(
                    DeliveryEvent(
                        delivery_id=shared_delivery.id,
                        event_type=DeliveryEventType.ASSIGNED,
                        actor="SYSTEM",
                        note="Cart payment confirmed; grouped delivery ready for assignment",
                    )
                )

        await _ensure_delivery_order_link(session, shared_delivery.id, order.id, sequence=idx)

    await session.commit()

    shared_delivery_id = shared_delivery.id if shared_delivery else None

    buyer = next((order.buyer for order in orders if order.buyer), None)
    if bot and buyer and buyer.telegram_id:
        try:
            await bot.send_message(
                chat_id=int(buyer.telegram_id),
                text=(
                    "Payment received successfully.\n\n"
                    f"{len(pending_orders)} order(s) confirmed.\n"
                    "Next step: we assign agent(s) and send you tracking."
                ),
            )
        except Exception:
            logger.exception("Failed to notify buyer for cart payment %s", reference)

    for order in pending_orders:
        seller_user = order.seller.user if order.seller else None
        if bot and seller_user and seller_user.telegram_id:
            try:
                seller_keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text=f"View Order #{order.id}", callback_data=f"seller_view_order_{order.id}")],
                        [InlineKeyboardButton(text="My Listings", callback_data="seller_listings")],
                    ]
                )
                await bot.send_message(
                    chat_id=int(seller_user.telegram_id),
                    text=(
                        f"You have a new paid order #{order.id}.\n"
                        f"Item: {order.quantity} x {order.listing.title if order.listing else 'Unknown item'}\n"
                        f"Delivery To: {order.buyer_address or 'N/A'}\n\n"
                        f"Please prepare {order.quantity} unit(s) for delivery."
                    ),
                    reply_markup=seller_keyboard,
                )
            except Exception:
                logger.exception("Failed to notify seller for cart-paid order %s", order.id)

    if shared_delivery_id is not None:
        await _notify_admins_new_paid_delivery(session, bot, shared_delivery_id, pending_orders)

    return {
        "status": "success",
        "message": "Cart payment processed",
        "order_ids": [order.id for order in orders],
    }


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
