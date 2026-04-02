"""Delivery notification service for agent assignment and status updates."""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from db.models import Delivery, DeliveryOrder, DeliveryAgent, Order, DeliveryStatus, SellerProfile
from core.config import get_settings

settings = get_settings()
bot_instance = None  # Will be set by bot initialization


def _full_name(first_name: str | None, last_name: str | None) -> str:
    return f"{first_name or ''} {last_name or ''}".strip() or "Unknown"


def set_bot_instance(bot):
    """Set the global bot instance for sending messages."""
    global bot_instance
    bot_instance = bot


async def notify_agent_delivery_assigned(delivery_id: int, session: AsyncSession):
    """
    Send Telegram notification to assigned delivery agent with multi-seller pickup details.
    
    Agent receives:
    - Pickup sequence (1️⃣ Seller A, 2️⃣ Seller B, etc.)
    - Room numbers and locations
    - Item details for each seller
    - [START PICKUP] button to begin workflow
    """
    if not bot_instance:
        return

    # Fetch delivery with all linked orders
    result = await session.execute(
        select(Delivery)
        .options(joinedload(Delivery.agent))
        .where(Delivery.id == delivery_id)
    )
    delivery = result.scalars().first()
    if not delivery or not delivery.agent or not delivery.agent.telegram_id:
        return

    # Get delivery orders sorted by sequence
    result = await session.execute(
        select(DeliveryOrder)
        .options(
            joinedload(DeliveryOrder.order).joinedload(Order.buyer),
            joinedload(DeliveryOrder.order).joinedload(Order.listing),
            joinedload(DeliveryOrder.order).joinedload(Order.seller).joinedload(SellerProfile.user),
        )
        .where(DeliveryOrder.delivery_id == delivery_id)
        .order_by(DeliveryOrder.sequence)
    )
    delivery_orders = result.scalars().all()
    if not delivery_orders:
        return

    # Build message text with pickup sequence
    message_lines = [
        f"📦 <b>New Delivery Job</b>\n",
        f"<b>Delivery ID:</b> {delivery_id}\n",
    ]

    # Get buyer info from first order (all orders go to same buyer)
    first_order = delivery_orders[0].order
    if first_order:
        buyer = first_order.buyer
        if buyer:
            message_lines.append(f"<b>Buyer:</b> {_full_name(buyer.first_name, buyer.last_name)}\n")
        message_lines.append(f"<b>Delivery To:</b> {first_order.buyer_address or 'TBD'}\n")

    message_lines.append("\n<b>Pickup Sequence:</b>\n")

    # Add each pickup stop
    total_items = 0
    for idx, delivery_order in enumerate(delivery_orders, 1):
        order = delivery_order.order
        if not order:
            continue

        total_items += order.quantity
        seller = order.seller
        seller_name = (
            _full_name(seller.user.first_name, seller.user.last_name) if seller and seller.user else "Unknown"
        )
        room = seller.room_number if seller else "N/A"
        hall = seller.hall if seller else "N/A"

        message_lines.append(
            f"{idx}️⃣ <b>{seller_name}</b>\n"
            f"   Room: {room}, Hall: {hall}\n"
            f"   Item: {order.quantity}× {order.listing.title}\n"
        )

    message_lines.append(f"\n<b>Total Items:</b> {total_items}\n")

    message_text = "".join(message_lines)

    # Build keyboard with START PICKUP button
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚚 START PICKUP", callback_data=f"delivery_start_pickup_{delivery_id}")],
            [InlineKeyboardButton(text="ℹ️ View Details", callback_data=f"delivery_details_{delivery_id}")],
        ]
    )

    try:
        sent_message = await bot_instance.send_message(
            chat_id=delivery.agent.telegram_id,
            text=message_text,
            parse_mode="HTML",
            reply_markup=keyboard
        )

        # TODO: Store message_id in Delivery model for later edits
        # For now, we're not tracking this but could add a field for in-place message updates
    except Exception as e:
        print(f"Failed to notify agent {delivery.agent.telegram_id}: {e}")


async def notify_buyer_delivery_status_update(
    order_id: int,
    status: str,
    agent: DeliveryAgent | None,
    session: AsyncSession | None = None,
):
    """
    Send notification to buyer when delivery status updates.
    
    Status can be: PICKED_UP, IN_TRANSIT, DELIVERED
    Agent is passed for IN_TRANSIT/DELIVERED notifications with contact info.
    """
    if not bot_instance:
        return

    # Fetch order and buyer
    if session:
        result = await session.execute(select(Order).options(joinedload(Order.buyer)).where(Order.id == order_id))
        order = result.scalars().first()
    else:
        # Can't proceed without session
        return

    if not order or not order.buyer or not order.buyer.telegram_id:
        return

    # Build status message
    if status == "PICKED_UP":
        message_text = (
            f"✅ <b>Your Order Picked Up</b>\n\n"
            f"Order #{order.id} has been collected from the seller.\n"
            f"Your delivery is being prepared for transit."
        )
    elif status == "IN_TRANSIT":
        message_text = (
            f"🚗 <b>Delivery In Transit</b>\n\n"
            f"Order #{order.id} is on the way to you!\n"
        )
        if agent:
            message_text += f"Driver: {agent.name}"
            if agent.phone:
                message_text += f" | {agent.phone}"
    elif status == "DELIVERED":
        message_text = (
            f"📦 <b>Order Delivered</b>\n\n"
            f"Order #{order.id} has been delivered!\n\n"
            f"Please confirm receipt within 4 hours to complete the transaction."
        )
    else:
        return

    try:
        await bot_instance.send_message(
            chat_id=order.buyer.telegram_id,
            text=message_text,
            parse_mode="HTML"
        )
    except Exception as e:
        print(f"Failed to notify buyer {order.buyer.telegram_id}: {e}")


async def update_agent_job_message(message_id: int, delivery_id: int, stage: str, agent_telegram_id: str):
    """
    Edit agent's original job message to show current pickup progress.
    
    Stages: pickup_in_progress, all_collected, in_transit, delivered
    """
    if not bot_instance or not message_id:
        return

    try:
        # Build updated message based on stage
        if stage == "all_collected":
            text = "✅ <b>All Items Collected</b>\n\nReady to proceed with delivery?"
        elif stage == "in_transit":
            text = "🚗 <b>Delivery In Transit</b>\n\nYour delivery is on the way!"
        elif stage == "delivered":
            text = "✅ <b>Delivery Completed</b>\n\nThank you for using our service!"
        else:
            return

        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

        # Build appropriate keyboard for stage
        if stage == "all_collected":
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🚗 In Transit", callback_data=f"delivery_in_transit_{delivery_id}")],
                ]
            )
        elif stage == "in_transit":
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="📍 Location", callback_data=f"delivery_location_{delivery_id}")],
                    [InlineKeyboardButton(text="✅ Delivered", callback_data=f"delivery_delivered_{delivery_id}")],
                ]
            )
        else:
            keyboard = None

        await bot_instance.edit_message_text(
            chat_id=agent_telegram_id,
            message_id=message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=keyboard
        )
    except Exception as e:
        print(f"Failed to update agent message {message_id}: {e}")
