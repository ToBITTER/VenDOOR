"""Delivery agent handlers for multi-seller pickup and delivery status updates via Telegram bot."""

from decimal import Decimal

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from db.models import Delivery, DeliveryAgent, DeliveryOrder, DeliveryStatus, Order, SellerProfile
from services.delivery_notifications import notify_buyer_delivery_status_update
from services.delivery_status import update_delivery_order_status, update_delivery_status

router = Router()


class DeliveryAgentStates(StatesGroup):
    """FSM states for delivery agent workflows."""

    awaiting_arrival_confirmation = State()
    awaiting_pickup_photo = State()
    awaiting_delivery_location = State()


def _parse_callback_id(callback_data: str | None, prefix: str) -> int | None:
    payload = (callback_data or "").strip()
    if not payload.startswith(prefix):
        return None
    value = payload.replace(prefix, "", 1).strip()
    if not value.isdigit():
        return None
    return int(value)


def _full_name(first_name: str | None, last_name: str | None) -> str:
    return f"{first_name or ''} {last_name or ''}".strip() or "Unknown"


async def safe_answer_callback(callback: CallbackQuery, text: str = "", show_alert: bool = False):
    """Safely answer callback query with error handling."""

    try:
        await callback.answer(text=text, show_alert=show_alert)
    except Exception:
        pass


async def _safe_edit_or_reply(callback: CallbackQuery, text: str, **kwargs):
    if callback.message is None:
        return
    try:
        await callback.message.edit_text(text, **kwargs)
    except Exception:
        await callback.message.answer(text, **kwargs)


async def get_agent_by_telegram_id(telegram_id: int, session: AsyncSession) -> DeliveryAgent | None:
    """Fetch delivery agent by Telegram user ID."""

    result = await session.execute(select(DeliveryAgent).where(DeliveryAgent.telegram_id == str(telegram_id)))
    return result.scalars().first()


async def get_delivery_with_orders(delivery_id: int, session: AsyncSession) -> Delivery | None:
    """Fetch delivery with joined order/seller/buyer relations to avoid lazy-load crashes."""

    result = await session.execute(
        select(Delivery)
        .options(
            joinedload(Delivery.agent),
            joinedload(Delivery.order).joinedload(Order.buyer),
            joinedload(Delivery.delivery_orders)
            .joinedload(DeliveryOrder.order)
            .joinedload(Order.listing),
            joinedload(Delivery.delivery_orders)
            .joinedload(DeliveryOrder.order)
            .joinedload(Order.seller)
            .joinedload(SellerProfile.user),
        )
        .where(Delivery.id == delivery_id)
    )
    return result.unique().scalars().first()


def _arrival_keyboard(delivery_id: int, delivery_order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="I am here", callback_data=f"delivery_arrived_{delivery_order_id}")],
            [InlineKeyboardButton(text="Cancel", callback_data=f"delivery_cancel_{delivery_id}")],
        ]
    )


def _delivery_progress_keyboard(delivery_id: int, stage: str) -> InlineKeyboardMarkup:
    if stage == "ready_in_transit":
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Mark In Transit", callback_data=f"delivery_in_transit_{delivery_id}")],
                [InlineKeyboardButton(text="Cancel", callback_data=f"delivery_cancel_{delivery_id}")],
            ]
        )
    if stage == "in_transit":
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Update Location", callback_data=f"delivery_location_{delivery_id}")],
                [InlineKeyboardButton(text="Mark Delivered", callback_data=f"delivery_delivered_{delivery_id}")],
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=[])


def _cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Cancel", callback_data="delivery_cancel_workflow")]]
    )


def _pickup_stop_text(order: Order, step_num: int, total_steps: int) -> str:
    seller_name = "Unknown"
    seller_room = "N/A"
    seller_hall = "N/A"
    if order.seller:
        seller_room = order.seller.room_number or "N/A"
        seller_hall = order.seller.hall or "N/A"
        if order.seller.user:
            seller_name = _full_name(order.seller.user.first_name, order.seller.user.last_name)

    listing_title = order.listing.title if order.listing else "Unknown item"
    return (
        f"<b>Pickup Stop {step_num}/{total_steps}</b>\n\n"
        f"<b>Seller:</b> {seller_name}\n"
        f"<b>Room:</b> {seller_room}\n"
        f"<b>Hall:</b> {seller_hall}\n\n"
        f"<b>Items:</b> {order.quantity} x {listing_title}\n\n"
        "Please confirm your arrival at this location."
    )


async def _show_next_seller_confirmation(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    delivery_id = data.get("delivery_id")
    order_index = int(data.get("order_index", 0))
    delivery_orders_data = data.get("delivery_orders_data", [])

    if order_index >= len(delivery_orders_data):
        await _safe_edit_or_reply(
            callback,
            "All items collected.\n\nReady to proceed with delivery?",
            reply_markup=_delivery_progress_keyboard(int(delivery_id), stage="ready_in_transit"),
        )
        return

    current_do_data = delivery_orders_data[order_index]
    order = await session.get(Order, int(current_do_data["order_id"]))
    if not order:
        await _safe_edit_or_reply(callback, "Order not found. Please retry the delivery flow.")
        return

    step_num = order_index + 1
    total_steps = len(delivery_orders_data)
    await _safe_edit_or_reply(
        callback,
        _pickup_stop_text(order, step_num, total_steps),
        parse_mode="HTML",
        reply_markup=_arrival_keyboard(int(delivery_id), int(current_do_data["id"])),
    )
    await state.set_state(DeliveryAgentStates.awaiting_arrival_confirmation)
    await state.update_data(
        current_delivery_order_id=int(current_do_data["id"]),
        current_order_id=int(current_do_data["order_id"]),
    )


async def _show_next_seller_confirmation_from_message(
    message: Message, state: FSMContext, session: AsyncSession
):
    data = await state.get_data()
    delivery_id = data.get("delivery_id")
    order_index = int(data.get("order_index", 0))
    delivery_orders_data = data.get("delivery_orders_data", [])

    if order_index >= len(delivery_orders_data):
        await message.answer(
            "All items collected.\n\nReady to proceed with delivery?",
            reply_markup=_delivery_progress_keyboard(int(delivery_id), stage="ready_in_transit"),
        )
        return

    current_do_data = delivery_orders_data[order_index]
    order = await session.get(Order, int(current_do_data["order_id"]))
    if not order:
        await message.answer("Order not found. Please retry the delivery flow.")
        return

    step_num = order_index + 1
    total_steps = len(delivery_orders_data)
    await message.answer(
        _pickup_stop_text(order, step_num, total_steps),
        parse_mode="HTML",
        reply_markup=_arrival_keyboard(int(delivery_id), int(current_do_data["id"])),
    )
    await state.set_state(DeliveryAgentStates.awaiting_arrival_confirmation)
    await state.update_data(
        current_delivery_order_id=int(current_do_data["id"]),
        current_order_id=int(current_do_data["order_id"]),
    )


@router.callback_query(F.data.startswith("delivery_start_pickup_"))
async def delivery_start_pickup(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    """Agent clicks START PICKUP to begin sequential multi-seller pickup workflow."""

    delivery_id = _parse_callback_id(callback.data, "delivery_start_pickup_")
    if delivery_id is None:
        await safe_answer_callback(callback, "Invalid delivery ID", show_alert=True)
        return

    delivery = await get_delivery_with_orders(delivery_id, session)
    if not delivery:
        await safe_answer_callback(callback, "Delivery not found", show_alert=True)
        return

    if callback.from_user is None:
        await safe_answer_callback(callback, "Unable to identify delivery agent", show_alert=True)
        return
    agent = await get_agent_by_telegram_id(callback.from_user.id, session)
    if not agent or delivery.agent_id != agent.id:
        await safe_answer_callback(callback, "Not assigned to this delivery", show_alert=True)
        return

    await safe_answer_callback(callback)

    delivery_orders = sorted(delivery.delivery_orders or [], key=lambda item: (item.sequence, item.id))
    if not delivery_orders:
        await _safe_edit_or_reply(callback, "No orders found in this delivery.")
        return

    await state.update_data(
        delivery_id=delivery_id,
        order_index=0,
        delivery_orders_data=[
            {"id": int(delivery_order.id), "order_id": int(delivery_order.order_id), "sequence": int(delivery_order.sequence)}
            for delivery_order in delivery_orders
        ],
    )
    await _show_next_seller_confirmation(callback, state, session)


@router.callback_query(F.data.startswith("delivery_arrived_"))
async def delivery_arrived_at_seller(callback: CallbackQuery, state: FSMContext):
    """Agent confirms arrival at seller location, then uploads pickup proof photo."""

    delivery_order_id = _parse_callback_id(callback.data, "delivery_arrived_")
    if delivery_order_id is None:
        await safe_answer_callback(callback, "Invalid delivery order ID", show_alert=True)
        return

    data = await state.get_data()
    expected_id = data.get("current_delivery_order_id")
    if expected_id is not None and int(expected_id) != delivery_order_id:
        await safe_answer_callback(callback, "This stop is no longer active", show_alert=True)
        return

    await safe_answer_callback(callback)
    await _safe_edit_or_reply(
        callback,
        "<b>Upload Pickup Photo</b>\n\nPlease send a photo of the collected items as pickup proof.",
        parse_mode="HTML",
        reply_markup=_cancel_keyboard(),
    )
    await state.set_state(DeliveryAgentStates.awaiting_pickup_photo)


@router.message(DeliveryAgentStates.awaiting_pickup_photo)
async def receive_pickup_photo(message: Message, state: FSMContext, session: AsyncSession):
    """Agent uploads pickup photo, then move to the next pickup stop."""

    if not message.photo:
        await message.reply("Please send a photo. Type /cancel to abort.")
        return

    data = await state.get_data()
    delivery_id = data.get("delivery_id")
    delivery_order_id = data.get("current_delivery_order_id")
    current_order_id = data.get("current_order_id")
    order_index = int(data.get("order_index", 0))
    delivery_orders_data = data.get("delivery_orders_data", [])

    if delivery_id is None or delivery_order_id is None or current_order_id is None:
        await state.clear()
        await message.reply("Pickup session expired. Please restart from your delivery card.")
        return

    photo_file_id = message.photo[-1].file_id
    updated = await update_delivery_order_status(
        int(delivery_order_id), session, note=f"Pickup proof photo: {photo_file_id}"
    )
    if not updated:
        await session.rollback()
        await message.reply("Could not update pickup state. Please retry.")
        return

    await session.commit()
    await notify_buyer_delivery_status_update(int(current_order_id), "PICKED_UP", None, session)

    next_index = order_index + 1
    await state.update_data(order_index=next_index)

    if next_index < len(delivery_orders_data):
        await message.answer("Pickup confirmed. Moving to next seller.")
        await _show_next_seller_confirmation_from_message(message, state, session)
        return

    await message.answer(
        "All items collected.\n\nReady to proceed with delivery?",
        reply_markup=_delivery_progress_keyboard(int(delivery_id), stage="ready_in_transit"),
    )
    await state.clear()


@router.callback_query(F.data.startswith("delivery_in_transit_"))
async def delivery_in_transit(callback: CallbackQuery, session: AsyncSession):
    """Agent marks delivery as IN_TRANSIT after all pickups."""

    delivery_id = _parse_callback_id(callback.data, "delivery_in_transit_")
    if delivery_id is None:
        await safe_answer_callback(callback, "Invalid delivery ID", show_alert=True)
        return

    delivery = await get_delivery_with_orders(delivery_id, session)
    if not delivery:
        await safe_answer_callback(callback, "Delivery not found", show_alert=True)
        return

    if callback.from_user is None:
        await safe_answer_callback(callback, "Unable to identify delivery agent", show_alert=True)
        return
    agent = await get_agent_by_telegram_id(callback.from_user.id, session)
    if not agent or delivery.agent_id != agent.id:
        await safe_answer_callback(callback, "Not assigned to this delivery", show_alert=True)
        return

    await safe_answer_callback(callback)
    await update_delivery_status(delivery_id, DeliveryStatus.IN_TRANSIT, "AGENT", None, session)
    await session.commit()

    for delivery_order in delivery.delivery_orders or []:
        await notify_buyer_delivery_status_update(
            int(delivery_order.order_id), DeliveryStatus.IN_TRANSIT.value, agent, session
        )

    await _safe_edit_or_reply(
        callback,
        "<b>Delivery In Transit</b>\n\nDelivery is on the way. You can share location or mark delivered.",
        parse_mode="HTML",
        reply_markup=_delivery_progress_keyboard(delivery_id, stage="in_transit"),
    )


@router.callback_query(F.data.startswith("delivery_location_"))
async def delivery_send_location(callback: CallbackQuery, state: FSMContext):
    """Agent initiates location update."""

    delivery_id = _parse_callback_id(callback.data, "delivery_location_")
    if delivery_id is None:
        await safe_answer_callback(callback, "Invalid delivery ID", show_alert=True)
        return

    await safe_answer_callback(callback)
    await _safe_edit_or_reply(
        callback,
        "<b>Send Location</b>\n\nShare your current location from the Telegram attachment menu.",
        parse_mode="HTML",
        reply_markup=_cancel_keyboard(),
    )
    await state.set_state(DeliveryAgentStates.awaiting_delivery_location)
    await state.update_data(delivery_id=delivery_id)


@router.message(DeliveryAgentStates.awaiting_delivery_location)
async def receive_delivery_location(message: Message, state: FSMContext, session: AsyncSession):
    """Agent sends location; update delivery coordinates."""

    if not message.location:
        await message.reply("Please share your location. Type /cancel to abort.")
        return

    data = await state.get_data()
    delivery_id = data.get("delivery_id")
    if delivery_id is None:
        await state.clear()
        await message.reply("Location session expired. Please restart from your delivery card.")
        return

    latitude = Decimal(str(message.location.latitude))
    longitude = Decimal(str(message.location.longitude))

    await update_delivery_status(
        int(delivery_id),
        DeliveryStatus.IN_TRANSIT,
        "AGENT",
        "Location update",
        session,
        latitude=latitude,
        longitude=longitude,
    )
    await session.commit()

    await message.answer(
        "Location recorded.\n\nContinue delivery when ready.",
        reply_markup=_delivery_progress_keyboard(int(delivery_id), stage="in_transit"),
    )
    await state.clear()


@router.callback_query(F.data.startswith("delivery_delivered_"))
async def delivery_mark_delivered(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    """Agent marks delivery as DELIVERED."""

    delivery_id = _parse_callback_id(callback.data, "delivery_delivered_")
    if delivery_id is None:
        await safe_answer_callback(callback, "Invalid delivery ID", show_alert=True)
        return

    delivery = await get_delivery_with_orders(delivery_id, session)
    if not delivery:
        await safe_answer_callback(callback, "Delivery not found", show_alert=True)
        return

    if callback.from_user is None:
        await safe_answer_callback(callback, "Unable to identify delivery agent", show_alert=True)
        return
    agent = await get_agent_by_telegram_id(callback.from_user.id, session)
    if not agent or delivery.agent_id != agent.id:
        await safe_answer_callback(callback, "Not assigned to this delivery", show_alert=True)
        return

    await safe_answer_callback(callback)
    await update_delivery_status(delivery_id, DeliveryStatus.DELIVERED, "AGENT", None, session)
    await session.commit()

    for delivery_order in delivery.delivery_orders or []:
        await notify_buyer_delivery_status_update(
            int(delivery_order.order_id), DeliveryStatus.DELIVERED.value, agent, session
        )

    await state.clear()
    await _safe_edit_or_reply(
        callback,
        "<b>Delivery Completed</b>\n\nThank you for the update.",
        parse_mode="HTML",
        reply_markup=None,
    )


@router.callback_query(F.data == "delivery_cancel_workflow")
@router.callback_query(F.data.startswith("delivery_cancel_"))
async def delivery_cancel_workflow(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_answer_callback(callback, "Delivery workflow cancelled.")
    await _safe_edit_or_reply(callback, "Workflow cancelled. Open your delivery card to resume.")
