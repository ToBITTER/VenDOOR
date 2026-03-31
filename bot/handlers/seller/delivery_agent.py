"""Delivery agent handlers for multi-seller pickup and delivery status updates via Telegram bot."""

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from sqlalchemy.ext.asyncio import AsyncSession
from decimal import Decimal

from db.models import Delivery, DeliveryAgent, Order, DeliveryStatus, DeliveryOrder
from services.delivery_notifications import (
    notify_buyer_delivery_status_update,
    update_agent_job_message,
)
from services.delivery_status import update_delivery_order_status, update_delivery_status

router = Router()


class DeliveryAgentStates(StatesGroup):
    """FSM states for delivery agent workflows."""
    awaiting_arrival_confirmation = State()
    awaiting_pickup_photo = State()
    awaiting_delivery_location = State()


async def safe_answer_callback(callback: CallbackQuery, text: str = "", show_alert: bool = False):
    """Safely answer callback query with error handling."""
    try:
        await callback.answer(text=text, show_alert=show_alert)
    except Exception:
        pass


async def safe_edit_text(callback: CallbackQuery, text: str, **kwargs):
    """Safely edit callback message text."""
    try:
        await callback.message.edit_text(text, **kwargs)
    except Exception:
        await callback.message.reply(text, **kwargs)


async def get_agent_by_telegram_id(telegram_id: int, session: AsyncSession) -> DeliveryAgent | None:
    """Fetch delivery agent by Telegram user ID."""
    from sqlalchemy import select
    result = await session.execute(
        select(DeliveryAgent).where(DeliveryAgent.telegram_id == str(telegram_id))
    )
    return result.scalars().first()


async def get_delivery_with_orders(delivery_id: int, session: AsyncSession) -> Delivery | None:
    """Fetch delivery with all linked orders (via DeliveryOrder join table)."""
    from sqlalchemy import select
    result = await session.execute(
        select(Delivery)
        .where(Delivery.id == delivery_id)
    )
    delivery = result.scalars().first()
    if delivery:
        await session.refresh(delivery, ["delivery_orders"])
    return delivery


@router.callback_query(F.data.startswith("delivery_start_pickup_"))
async def delivery_start_pickup(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    """Agent clicks [START PICKUP] to begin sequential multi-seller pickup workflow."""
    try:
        delivery_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        await safe_answer_callback(callback, "Invalid delivery ID", show_alert=True)
        return

    delivery = await get_delivery_with_orders(delivery_id, session)
    if not delivery:
        await safe_answer_callback(callback, "Delivery not found", show_alert=True)
        return

    agent = await get_agent_by_telegram_id(callback.from_user.id, session)
    if not agent or delivery.agent_id != agent.id:
        await safe_answer_callback(callback, "Not assigned to this delivery", show_alert=True)
        return

    await safe_answer_callback(callback)

    # Get all orders sorted by sequence
    delivery_orders = sorted(delivery.delivery_orders, key=lambda x: x.sequence)
    if not delivery_orders:
        await callback.message.reply("❌ No orders found in this delivery.")
        return

    # Store delivery info in state for multi-step workflow
    await state.update_data(
        delivery_id=delivery_id,
        order_index=0,
        delivery_orders_data=[
            {"id": do.id, "order_id": do.order_id, "sequence": do.sequence}
            for do in delivery_orders
        ]
    )

    # Show first seller
    await _show_next_seller_confirmation(callback, state, session)


async def _show_next_seller_confirmation(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    """Display next seller location and request arrival confirmation."""
    data = await state.get_data()
    delivery_id = data.get("delivery_id")
    order_index = data.get("order_index", 0)
    delivery_orders_data = data.get("delivery_orders_data", [])

    if order_index >= len(delivery_orders_data):
        # All sellers done - show [IN_TRANSIT] button
        await callback.message.edit_text(
            "✅ All items collected!\n\n"
            "Ready to proceed with delivery?",
            reply_markup=__get_delivery_progress_keyboard(delivery_id, stage="ready_in_transit")
        )
        return

    current_do_data = delivery_orders_data[order_index]
    order = await session.get(Order, current_do_data["order_id"])
    if not order:
        await callback.message.reply("❌ Order not found")
        return

    seller_name = order.seller.user.full_name if order.seller and order.seller.user else "Unknown"
    seller_room = order.seller.room_number if order.seller else "N/A"
    seller_hall = order.seller.hall if order.seller else "N/A"

    step_num = order_index + 1
    total_steps = len(delivery_orders_data)

    message_text = (
        f"📍 <b>Stop {step_num}/{total_steps}</b>\n\n"
        f"<b>Seller:</b> {seller_name}\n"
        f"<b>Room:</b> {seller_room}\n"
        f"<b>Hall:</b> {seller_hall}\n\n"
        f"<b>Items:</b> {order.quantity} × {order.listing.title}\n\n"
        "Please confirm your arrival at this location."
    )

    await callback.message.edit_text(
        message_text,
        parse_mode="HTML",
        reply_markup=__get_arrival_confirmation_keyboard(delivery_id, current_do_data["id"])
    )

    await state.set_state(DeliveryAgentStates.awaiting_arrival_confirmation)
    await state.update_data(current_delivery_order_id=current_do_data["id"], current_order_id=current_do_data["order_id"])


@router.callback_query(F.data.startswith("delivery_arrived_"))
async def delivery_arrived_at_seller(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    """Agent confirms arrival at seller location, now request photo upload."""
    try:
        delivery_order_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        await safe_answer_callback(callback, "Invalid delivery order ID", show_alert=True)
        return

    await safe_answer_callback(callback)

    data = await state.get_data()
    current_order_id = data.get("current_order_id")

    message_text = (
        "📸 <b>Upload Pickup Photo</b>\n\n"
        "Please send a photo of the items being collected as proof of pickup."
    )

    await callback.message.edit_text(
        message_text,
        parse_mode="HTML",
        reply_markup=__get_cancel_keyboard()
    )

    await state.set_state(DeliveryAgentStates.awaiting_pickup_photo)


@router.message(DeliveryAgentStates.awaiting_pickup_photo)
async def receive_pickup_photo(message: Message, state: FSMContext, session: AsyncSession):
    """Agent uploads pickup photo, mark order as picked up."""
    if not message.photo:
        await message.reply("Please send a photo. Type /cancel to abort.")
        return

    data = await state.get_data()
    delivery_id = data.get("delivery_id")
    delivery_order_id = data.get("current_delivery_order_id")
    current_order_id = data.get("current_order_id")
    order_index = data.get("order_index", 0)
    delivery_orders_data = data.get("delivery_orders_data", [])

    # Get photo file_id
    photo_file_id = message.photo[-1].file_id

    # Update delivery order status with photo reference
    delivery_order = await session.get(DeliveryOrder, delivery_order_id)
    if delivery_order:
        from datetime import datetime, timezone
        delivery_order.picked_up_at = datetime.now(timezone.utc)
        session.add(delivery_order)

        # Update order status to PICKED_UP
        order = await session.get(Order, current_order_id)
        if order:
            order.status = "PICKED_UP"  # Assuming this status exists
            session.add(order)

        await session.commit()

    # Notify buyer that their order was picked up
    await notify_buyer_delivery_status_update(current_order_id, "PICKED_UP", None, session)

    # Move to next seller
    next_index = order_index + 1
    await state.update_data(order_index=next_index)

    # Show next seller or mark all collected
    if next_index < len(delivery_orders_data):
        # More sellers to pick up from
        await message.reply(
            "✅ Photo received. Proceeding to next seller...",
            reply_markup=None
        )
        # Store message object and call the next seller flow
        # Create a fake callback for the next step
        await state.update_data(message_id=message.message_id)
        await _show_next_seller_confirmation_from_message(message, state, session)
    else:
        # All items collected
        await message.reply(
            "✅ All items collected!\n\n"
            "Ready to proceed with delivery?",
            reply_markup=__get_delivery_progress_keyboard(delivery_id, stage="ready_in_transit")
        )
        await state.clear()


async def _show_next_seller_confirmation_from_message(message: Message, state: FSMContext, session: AsyncSession):
    """Helper to show next seller after photo via message (not callback)."""
    data = await state.get_data()
    delivery_id = data.get("delivery_id")
    order_index = data.get("order_index", 0)
    delivery_orders_data = data.get("delivery_orders_data", [])

    if order_index >= len(delivery_orders_data):
        return

    current_do_data = delivery_orders_data[order_index]
    order = await session.get(Order, current_do_data["order_id"])
    if not order:
        await message.reply("❌ Order not found")
        return

    seller_name = order.seller.user.full_name if order.seller and order.seller.user else "Unknown"
    seller_room = order.seller.room_number if order.seller else "N/A"
    seller_hall = order.seller.hall if order.seller else "N/A"

    step_num = order_index + 1
    total_steps = len(delivery_orders_data)

    message_text = (
        f"📍 <b>Stop {step_num}/{total_steps}</b>\n\n"
        f"<b>Seller:</b> {seller_name}\n"
        f"<b>Room:</b> {seller_room}\n"
        f"<b>Hall:</b> {seller_hall}\n\n"
        f"<b>Items:</b> {order.quantity} × {order.listing.title}\n\n"
        "Please confirm your arrival at this location."
    )

    await message.reply(
        message_text,
        parse_mode="HTML",
        reply_markup=__get_arrival_confirmation_keyboard(delivery_id, current_do_data["id"])
    )

    await state.set_state(DeliveryAgentStates.awaiting_arrival_confirmation)
    await state.update_data(current_delivery_order_id=current_do_data["id"], current_order_id=current_do_data["order_id"])


@router.callback_query(F.data.startswith("delivery_in_transit_"))
async def delivery_in_transit(callback: CallbackQuery, session: AsyncSession):
    """Agent marks delivery as IN_TRANSIT after all pickups."""
    try:
        delivery_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        await safe_answer_callback(callback, "Invalid delivery ID", show_alert=True)
        return

    delivery = await get_delivery_with_orders(delivery_id, session)
    if not delivery:
        await safe_answer_callback(callback, "Delivery not found", show_alert=True)
        return

    agent = await get_agent_by_telegram_id(callback.from_user.id, session)
    if not agent or delivery.agent_id != agent.id:
        await safe_answer_callback(callback, "Not assigned to this delivery", show_alert=True)
        return

    await safe_answer_callback(callback)

    # Update delivery status to IN_TRANSIT
    await update_delivery_status(delivery_id, DeliveryStatus.IN_TRANSIT, "AGENT", None, session)
    await session.commit()

    # Notify all buyers (could be multiple orders linked to this delivery)
    for delivery_order in delivery.delivery_orders:
        await notify_buyer_delivery_status_update(delivery_order.order.id, DeliveryStatus.IN_TRANSIT, agent, session)

    message_text = (
        "🚗 <b>Delivery In Transit</b>\n\n"
        "Your delivery is on the way. You can share location or mark as delivered."
    )

    await callback.message.edit_text(
        message_text,
        parse_mode="HTML",
        reply_markup=__get_delivery_progress_keyboard(delivery_id, stage="in_transit")
    )


@router.callback_query(F.data.startswith("delivery_location_"))
async def delivery_send_location(callback: CallbackQuery, state: FSMContext):
    """Agent initiates sending location update."""
    try:
        delivery_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        await safe_answer_callback(callback, "Invalid delivery ID", show_alert=True)
        return

    await safe_answer_callback(callback)

    message_text = (
        "📍 <b>Send Location</b>\n\n"
        "Please share your current location (live location or coordinates).\n\n"
        "Tap the attachment button and select 'Location'."
    )

    await callback.message.edit_text(
        message_text,
        parse_mode="HTML",
        reply_markup=__get_cancel_keyboard()
    )

    await state.set_state(DeliveryAgentStates.awaiting_delivery_location)
    await state.update_data(delivery_id=delivery_id)


@router.message(DeliveryAgentStates.awaiting_delivery_location)
async def receive_delivery_location(message: Message, state: FSMContext, session: AsyncSession):
    """Agent sends location, update delivery coordinates."""
    if not message.location:
        await message.reply("Please share your location. Type /cancel to abort.")
        return

    data = await state.get_data()
    delivery_id = data.get("delivery_id")

    latitude = Decimal(str(message.location.latitude))
    longitude = Decimal(str(message.location.longitude))

    await update_delivery_status(
        delivery_id,
        DeliveryStatus.IN_TRANSIT,
        "AGENT",
        "Location update",
        session,
        latitude=latitude,
        longitude=longitude
    )
    await session.commit()

    await message.reply(
        "✅ Location recorded.\n\n"
        "Ready to continue?",
        reply_markup=__get_delivery_progress_keyboard(delivery_id, stage="in_transit")
    )
    await state.clear()


@router.callback_query(F.data.startswith("delivery_delivered_"))
async def delivery_mark_delivered(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    """Agent marks delivery as DELIVERED."""
    try:
        delivery_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        await safe_answer_callback(callback, "Invalid delivery ID", show_alert=True)
        return

    delivery = await get_delivery_with_orders(delivery_id, session)
    if not delivery:
        await safe_answer_callback(callback, "Delivery not found", show_alert=True)
        return

    agent = await get_agent_by_telegram_id(callback.from_user.id, session)
    if not agent or delivery.agent_id != agent.id:
        await safe_answer_callback(callback, "Not assigned to this delivery", show_alert=True)
        return

    await safe_answer_callback(callback)

    # Update delivery status
    await update_delivery_status(delivery_id, DeliveryStatus.DELIVERED, "AGENT", None, session)
    await session.commit()

    # Notify buyer
    if delivery.order:
        await notify_buyer_delivery_status_update(delivery.order.id, DeliveryStatus.DELIVERED, agent, session)

    await callback.message.edit_text(
        "✅ <b>Delivery Completed</b>\n\n"
        "Thank you for the delivery!",
        parse_mode="HTML",
        reply_markup=None
    )


# Helper keyboard builders
def __get_arrival_confirmation_keyboard(delivery_id: int, delivery_order_id: int):
    """Keyboard for agent to confirm arrival at seller."""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ I'm Here", callback_data=f"delivery_arrived_{delivery_order_id}")],
            [InlineKeyboardButton(text="❌ Cancel", callback_data=f"delivery_cancel_{delivery_id}")],
        ]
    )


def __get_delivery_progress_keyboard(delivery_id: int, stage: str):
    """Keyboard for delivery progress based on stage."""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    if stage == "ready_in_transit":
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🚗 In Transit", callback_data=f"delivery_in_transit_{delivery_id}")],
                [InlineKeyboardButton(text="❌ Cancel", callback_data=f"delivery_cancel_{delivery_id}")],
            ]
        )
    elif stage == "in_transit":
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📍 Update Location", callback_data=f"delivery_location_{delivery_id}")],
                [InlineKeyboardButton(text="✅ Delivered", callback_data=f"delivery_delivered_{delivery_id}")],
            ]
        )
    
    return InlineKeyboardMarkup(inline_keyboard=[])


def __get_cancel_keyboard():
    """Keyboard with cancel button."""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Cancel", callback_data="delivery_cancel_workflow")],
        ]
    )
