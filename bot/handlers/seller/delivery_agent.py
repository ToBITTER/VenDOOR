"""Delivery agent handlers for multi-seller pickup and delivery status updates via Telegram bot."""

from decimal import Decimal
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from db.models import AdminUser, Delivery, DeliveryAgent, DeliveryOrder, DeliveryStatus, Order, SellerProfile
from core.config import get_settings
from services.delivery_notifications import (
    notify_buyer_all_pickups_completed,
    notify_buyer_group_delivery_status_update,
)
from services.delivery_status import update_delivery_order_status, update_delivery_status

router = Router()
settings = get_settings()


class DeliveryAgentStates(StatesGroup):
    """FSM states for delivery agent workflows."""

    awaiting_arrival_confirmation = State()
    awaiting_pickup_photo = State()
    awaiting_delivery_location = State()
    awaiting_profile_name = State()
    awaiting_profile_phone = State()
    awaiting_profile_vehicle = State()


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


def _seller_display_name(order: Order) -> str:
    if order.seller:
        if order.seller.full_name:
            return order.seller.full_name.strip()
        if order.seller.user:
            return _full_name(order.seller.user.first_name, order.seller.user.last_name)
    return "Unknown"


def _seller_location(order: Order) -> str:
    if not order.seller:
        return "Location unavailable"
    hall = (order.seller.hall or "").strip()
    room = (order.seller.room_number or "").strip()
    address = (order.seller.address or "").strip()
    if hall and room:
        return f"{hall}, Room {room}"
    if hall:
        return hall
    if address:
        return address
    return "Location unavailable"


def _delivery_order_payload(delivery_order: DeliveryOrder) -> dict:
    order = delivery_order.order
    if not order:
        return {
            "id": int(delivery_order.id),
            "order_id": int(delivery_order.order_id),
            "sequence": int(delivery_order.sequence),
            "seller_name": "Unknown",
            "seller_location": "Location unavailable",
            "item_title": "Item",
            "quantity": 1,
        }
    return {
        "id": int(delivery_order.id),
        "order_id": int(order.id),
        "sequence": int(delivery_order.sequence),
        "seller_name": _seller_display_name(order),
        "seller_location": _seller_location(order),
        "item_title": order.listing.title if order.listing else "Item",
        "quantity": int(order.quantity),
    }


def _build_pickup_stops(delivery_orders: list[DeliveryOrder]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for delivery_order in sorted(delivery_orders, key=lambda item: (item.sequence, item.id)):
        payload = _delivery_order_payload(delivery_order)
        order = delivery_order.order
        if order and order.seller_id is not None:
            group_key = f"seller:{order.seller_id}"
        else:
            group_key = f"legacy:{delivery_order.id}"

        entry = grouped.get(group_key)
        if not entry:
            entry = {
                "id": payload["id"],  # First delivery_order id used for callback identity.
                "order_id": payload["order_id"],
                "sequence": payload["sequence"],
                "seller_name": payload["seller_name"],
                "seller_location": payload["seller_location"],
                "delivery_order_ids": [],
                "items": [],
            }
            grouped[group_key] = entry

        entry["delivery_order_ids"].append(payload["id"])
        entry["items"].append(
            {
                "title": payload["item_title"],
                "quantity": int(payload["quantity"]),
            }
        )

    stops = list(grouped.values())
    stops.sort(key=lambda item: (int(item.get("sequence", 0)), int(item.get("id", 0))))
    return stops


async def _notify_admins_new_agent_application(
    message: Message, agent: DeliveryAgent, session: AsyncSession
) -> None:
    """
    Notify super admin and ops admins when a new delivery agent profile is submitted.
    """
    recipient_ids: set[int] = set()

    if settings.admin_telegram_id and str(settings.admin_telegram_id).isdigit():
        recipient_ids.add(int(settings.admin_telegram_id))

    result = await session.execute(select(AdminUser.telegram_id))
    for telegram_id in result.scalars().all():
        if telegram_id and str(telegram_id).isdigit():
            recipient_ids.add(int(telegram_id))

    if not recipient_ids:
        return

    status_text = "ACTIVE" if agent.is_active else "PENDING_ACTIVATION"
    applicant_username = (
        f"@{message.from_user.username}" if message.from_user and message.from_user.username else "N/A"
    )
    notification_text = (
        "<b>New Delivery Agent Application</b>\n\n"
        f"<b>Agent ID:</b> {agent.id}\n"
        f"<b>Name:</b> {agent.name}\n"
        f"<b>Phone:</b> {agent.phone or 'N/A'}\n"
        f"<b>Vehicle:</b> {agent.vehicle_type or 'N/A'}\n"
        f"<b>Telegram ID:</b> <code>{agent.telegram_id}</code>\n"
        f"<b>Username:</b> {applicant_username}\n"
        f"<b>Status:</b> {status_text}\n\n"
        "Open Admin Tools > Delivery Agents to review."
    )

    for chat_id in recipient_ids:
        try:
            await message.bot.send_message(chat_id=chat_id, text=notification_text, parse_mode="HTML")
        except Exception:
            pass


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


async def _ensure_delivery_order_link(session: AsyncSession, delivery: Delivery) -> None:
    if delivery.delivery_orders:
        return
    if not delivery.order_id:
        return
    existing = await session.execute(
        select(DeliveryOrder).where(
            DeliveryOrder.delivery_id == delivery.id,
            DeliveryOrder.order_id == delivery.order_id,
        )
    )
    if existing.scalars().first():
        return
    session.add(DeliveryOrder(delivery_id=delivery.id, order_id=delivery.order_id, sequence=1))
    await session.flush()


def _delivery_hub_keyboard(is_agent: bool, delivery_id: int | None = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if is_agent:
        rows.append([InlineKeyboardButton(text="Refresh Jobs", callback_data="delivery_hub")])
        if delivery_id is not None:
            rows.append([InlineKeyboardButton(text="Open Latest Job", callback_data=f"delivery_open_{delivery_id}")])
    else:
        rows.append([InlineKeyboardButton(text="Become Delivery Agent", callback_data="delivery_agent_signup")])
    rows.append([InlineKeyboardButton(text="Back", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "delivery_hub")
async def delivery_hub(callback: CallbackQuery, session: AsyncSession):
    if callback.from_user is None:
        await safe_answer_callback(callback, "Unable to identify your account", show_alert=True)
        return
    await safe_answer_callback(callback)

    agent = await get_agent_by_telegram_id(callback.from_user.id, session)
    if not agent:
        await _safe_edit_or_reply(
            callback,
            "<b>Delivery Hub</b>\n\nYou are not yet a delivery agent.\nTap below to create your agent profile.",
            parse_mode="HTML",
            reply_markup=_delivery_hub_keyboard(is_agent=False),
        )
        return

    if not agent.is_active:
        await _safe_edit_or_reply(
            callback,
            "<b>Delivery Hub</b>\n\nYour agent profile is pending activation by admin.",
            parse_mode="HTML",
            reply_markup=_delivery_hub_keyboard(is_agent=True),
        )
        return

    result = await session.execute(
        select(Delivery)
        .options(joinedload(Delivery.order).joinedload(Order.listing))
        .where(Delivery.agent_id == agent.id)
        .where(
            Delivery.status.in_(
                [
                    DeliveryStatus.ASSIGNED,
                    DeliveryStatus.PICKED_UP,
                    DeliveryStatus.IN_TRANSIT,
                ]
            )
        )
        .order_by(Delivery.updated_at.desc())
        .limit(10)
    )
    jobs = result.unique().scalars().all()

    if not jobs:
        await _safe_edit_or_reply(
            callback,
            (
                "<b>Delivery Hub</b>\n\n"
                "No active jobs right now.\n"
                "As soon as admin assigns one, it appears here automatically."
            ),
            parse_mode="HTML",
            reply_markup=_delivery_hub_keyboard(is_agent=True),
        )
        return

    lines = ["<b>Delivery Hub</b>\n", "Your active jobs:\n"]
    keyboard_rows: list[list[InlineKeyboardButton]] = []
    for job in jobs:
        title = job.order.listing.title if job.order and job.order.listing else "Order"
        lines.append(f"- Delivery #{job.id} | {job.status.value} | {title}")
        keyboard_rows.append(
            [InlineKeyboardButton(text=f"Open Delivery #{job.id}", callback_data=f"delivery_open_{job.id}")]
        )
    keyboard_rows.append([InlineKeyboardButton(text="Refresh Jobs", callback_data="delivery_hub")])
    keyboard_rows.append([InlineKeyboardButton(text="Back", callback_data="back_to_menu")])

    await _safe_edit_or_reply(
        callback,
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_rows),
    )


@router.callback_query(F.data == "delivery_agent_signup")
async def delivery_agent_signup_start(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    if callback.from_user is None:
        await safe_answer_callback(callback, "Unable to identify your account", show_alert=True)
        return
    await safe_answer_callback(callback)

    existing = await get_agent_by_telegram_id(callback.from_user.id, session)
    if existing:
        if existing.is_active:
            await _safe_edit_or_reply(
                callback,
                "You already have an active agent profile. Open Delivery Hub to see jobs.",
                reply_markup=_delivery_hub_keyboard(is_agent=True),
            )
        else:
            await _safe_edit_or_reply(
                callback,
                "You already signed up. Waiting for admin activation.",
                reply_markup=_delivery_hub_keyboard(is_agent=True),
            )
        return

    await state.set_state(DeliveryAgentStates.awaiting_profile_name)
    await _safe_edit_or_reply(
        callback,
        "Great, let's set up your agent profile.\n\nSend your full name:",
        reply_markup=_cancel_keyboard(),
    )


@router.message(DeliveryAgentStates.awaiting_profile_name)
async def delivery_agent_signup_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if len(name) < 2:
        await message.answer("Please send a valid full name (at least 2 characters).")
        return
    await state.update_data(name=name)
    await state.set_state(DeliveryAgentStates.awaiting_profile_phone)
    await message.answer(
        "Send your phone number (or type `skip`).",
        parse_mode="Markdown",
        reply_markup=_cancel_keyboard(),
    )


@router.message(DeliveryAgentStates.awaiting_profile_phone)
async def delivery_agent_signup_phone(message: Message, state: FSMContext):
    phone = (message.text or "").strip()
    if phone.lower() == "skip":
        phone = ""
    await state.update_data(phone=phone)
    await state.set_state(DeliveryAgentStates.awaiting_profile_vehicle)
    await message.answer(
        "What vehicle do you use? (bike / bicycle / car, or type `skip`)",
        parse_mode="Markdown",
        reply_markup=_cancel_keyboard(),
    )


@router.message(DeliveryAgentStates.awaiting_profile_vehicle)
async def delivery_agent_signup_vehicle(message: Message, state: FSMContext, session: AsyncSession):
    if message.from_user is None:
        await state.clear()
        await message.answer("Unable to identify your account. Please restart with /start.")
        return

    vehicle = (message.text or "").strip()
    if vehicle.lower() == "skip":
        vehicle = ""
    data = await state.get_data()
    name = (data.get("name") or message.from_user.full_name or "Delivery Agent").strip()
    phone = (data.get("phone") or "").strip() or None
    vehicle_type = vehicle or None
    telegram_id = str(message.from_user.id)

    agent = DeliveryAgent(
        name=name,
        telegram_id=telegram_id,
        phone=phone,
        vehicle_type=vehicle_type,
        is_active=settings.delivery_agent_self_signup_auto_activate,
    )
    session.add(agent)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        await state.clear()
        await message.answer("This Telegram account is already linked to an agent profile.")
        return

    await state.clear()
    await message.answer(
        (
            "<b>Agent Profile Created</b>\n\n"
            "Your profile is active.\n"
            "Use Delivery Hub from the main menu to view jobs and action buttons."
            if settings.delivery_agent_self_signup_auto_activate
            else "<b>Agent Profile Created</b>\n\n"
            "Your profile is pending admin activation.\n"
            "You'll see jobs as soon as activation is approved."
        ),
        parse_mode="HTML",
        reply_markup=_delivery_hub_keyboard(is_agent=True),
    )
    await _notify_admins_new_agent_application(message, agent, session)


@router.callback_query(F.data.startswith("delivery_open_"))
@router.callback_query(F.data.startswith("delivery_details_"))
async def delivery_open_job(callback: CallbackQuery, session: AsyncSession):
    delivery_id = _parse_callback_id(callback.data, "delivery_open_")
    if delivery_id is None:
        delivery_id = _parse_callback_id(callback.data, "delivery_details_")
    if delivery_id is None:
        await safe_answer_callback(callback, "Invalid delivery ID", show_alert=True)
        return
    if callback.from_user is None:
        await safe_answer_callback(callback, "Unable to identify delivery agent", show_alert=True)
        return
    await safe_answer_callback(callback)

    delivery = await get_delivery_with_orders(delivery_id, session)
    if not delivery:
        await _safe_edit_or_reply(callback, "Delivery not found.")
        return
    agent = await get_agent_by_telegram_id(callback.from_user.id, session)
    if not agent or delivery.agent_id != agent.id:
        await _safe_edit_or_reply(callback, "This delivery is not assigned to you.")
        return

    await _ensure_delivery_order_link(session, delivery)
    await session.commit()
    delivery = await get_delivery_with_orders(delivery_id, session)
    if not delivery:
        await _safe_edit_or_reply(callback, "Delivery not found.")
        return

    if delivery.status in (DeliveryStatus.ASSIGNED, DeliveryStatus.PENDING_ASSIGNMENT):
        delivery_orders = sorted(delivery.delivery_orders or [], key=lambda item: (item.sequence, item.id))
        if not delivery_orders:
            await _safe_edit_or_reply(
                callback,
                "No order assigned to this delivery yet. Please refresh from Delivery Hub.",
                reply_markup=_delivery_hub_keyboard(is_agent=True),
            )
            return
        await _safe_edit_or_reply(
            callback,
            "<b>Ready for Pickup</b>\n\nTap START PICKUP to begin.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="START PICKUP", callback_data=f"delivery_start_pickup_{delivery.id}")],
                    [InlineKeyboardButton(text="Back to Hub", callback_data="delivery_hub")],
                ]
            ),
        )
        return

    if delivery.status == DeliveryStatus.PICKED_UP:
        await _safe_edit_or_reply(
            callback,
            "<b>Items Picked Up</b>\n\nTap below to mark this delivery in transit.",
            parse_mode="HTML",
            reply_markup=_delivery_progress_keyboard(delivery.id, stage="ready_in_transit"),
        )
        return

    if delivery.status == DeliveryStatus.IN_TRANSIT:
        await _safe_edit_or_reply(
            callback,
            "<b>Delivery In Transit</b>\n\nUse the buttons below for live updates.",
            parse_mode="HTML",
            reply_markup=_delivery_progress_keyboard(delivery.id, stage="in_transit"),
        )
        return

    await _safe_edit_or_reply(
        callback,
        f"Delivery is currently {delivery.status.value}.",
        reply_markup=_delivery_hub_keyboard(is_agent=True),
    )


def _arrival_keyboard(delivery_id: int, delivery_order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="I am here", callback_data=f"delivery_arrived_{delivery_order_id}")],
            [InlineKeyboardButton(text="Back to Hub", callback_data="delivery_hub")],
        ]
    )


def _post_pickup_next_keyboard(delivery_id: int, has_next: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if has_next:
        rows.append(
            [InlineKeyboardButton(text="Proceed to Next Location", callback_data=f"delivery_proceed_next_{delivery_id}")]
        )
    rows.append([InlineKeyboardButton(text="Open Another Order", callback_data="delivery_hub")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _delivery_progress_keyboard(delivery_id: int, stage: str) -> InlineKeyboardMarkup:
    if stage == "ready_in_transit":
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Mark In Transit", callback_data=f"delivery_in_transit_{delivery_id}")],
                [InlineKeyboardButton(text="Back to Hub", callback_data="delivery_hub")],
            ]
        )
    if stage == "in_transit":
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Update Location", callback_data=f"delivery_location_{delivery_id}")],
                [InlineKeyboardButton(text="Mark Delivered", callback_data=f"delivery_delivered_{delivery_id}")],
                [InlineKeyboardButton(text="Back to Hub", callback_data="delivery_hub")],
            ]
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Back to Hub", callback_data="delivery_hub")]]
    )


def _cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Cancel Workflow", callback_data="delivery_cancel_workflow")],
            [InlineKeyboardButton(text="Back to Hub", callback_data="delivery_hub")],
        ]
    )


def _parse_manual_location_input(text: str) -> tuple[Decimal | None, Decimal | None, str | None]:
    """
    Accept either:
    - "lat,lon"
    - "lat,lon | note"
    - free text note only (manual location description)
    """
    raw = (text or "").strip()
    if not raw:
        return None, None, None

    parts = [segment.strip() for segment in raw.split("|", 1)]
    location_part = parts[0]
    note = parts[1] if len(parts) == 2 and parts[1] else None

    if "," in location_part:
        maybe_coords = [token.strip() for token in location_part.split(",", 1)]
        try:
            lat = Decimal(maybe_coords[0])
            lon = Decimal(maybe_coords[1])
            if Decimal("-90") <= lat <= Decimal("90") and Decimal("-180") <= lon <= Decimal("180"):
                return lat, lon, note
        except Exception:
            pass

    # Manual text note only
    return None, None, raw


def _pickup_stop_text(step_num: int, total_steps: int, stop_data: dict) -> str:
    seller_name = stop_data.get("seller_name", "Unknown")
    seller_location = stop_data.get("seller_location", "Location unavailable")
    items = stop_data.get("items", [])
    item_lines = []
    total_qty = 0
    for item in items:
        qty = int(item.get("quantity", 0))
        title = str(item.get("title") or "Item")
        total_qty += qty
        item_lines.append(f"- {qty} x {title}")

    if not item_lines:
        item_lines.append("- 1 x Item")
        total_qty = max(total_qty, 1)

    return (
        f"<b>Pickup Stop {step_num} of {total_steps}</b>\n\n"
        f"<b>Seller:</b> {seller_name}\n"
        f"<b>Pickup Location:</b> {seller_location}\n"
        f"<b>Total Units:</b> {total_qty}\n"
        f"<b>Items:</b>\n" + "\n".join(item_lines) + "\n\n"
        "Confirm arrival when you reach this pickup location."
    )


def _pickup_checklist(delivery_orders_data: list[dict], current_index: int) -> str:
    lines = ["", "<b>Pickup Progress</b>"]
    for idx, delivery_order_data in enumerate(delivery_orders_data, start=1):
        if idx - 1 < current_index:
            marker = "[DONE]"
        elif idx - 1 == current_index:
            marker = "[NEXT]"
        else:
            marker = "[PENDING]"
        seller_name = delivery_order_data.get("seller_name", "Seller")
        seller_location = delivery_order_data.get("seller_location", "Location unavailable")
        lines.append(f"{marker} Stop {idx}: {seller_name} - {seller_location}")
    return "\n".join(lines)


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

    step_num = order_index + 1
    total_steps = len(delivery_orders_data)
    await _safe_edit_or_reply(
        callback,
        _pickup_stop_text(step_num, total_steps, current_do_data) + _pickup_checklist(delivery_orders_data, order_index),
        parse_mode="HTML",
        reply_markup=_arrival_keyboard(int(delivery_id), int(current_do_data["id"])),
    )
    await state.set_state(DeliveryAgentStates.awaiting_arrival_confirmation)
    await state.update_data(
        current_delivery_order_id=int(current_do_data["id"]),
        current_order_id=int(current_do_data["order_id"]),
        current_delivery_order_ids=[int(value) for value in current_do_data.get("delivery_order_ids", [int(current_do_data["id"])])],
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

    step_num = order_index + 1
    total_steps = len(delivery_orders_data)
    await message.answer(
        _pickup_stop_text(step_num, total_steps, current_do_data) + _pickup_checklist(delivery_orders_data, order_index),
        parse_mode="HTML",
        reply_markup=_arrival_keyboard(int(delivery_id), int(current_do_data["id"])),
    )
    await state.set_state(DeliveryAgentStates.awaiting_arrival_confirmation)
    await state.update_data(
        current_delivery_order_id=int(current_do_data["id"]),
        current_order_id=int(current_do_data["order_id"]),
        current_delivery_order_ids=[int(value) for value in current_do_data.get("delivery_order_ids", [int(current_do_data["id"])])],
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

    await _ensure_delivery_order_link(session, delivery)
    await session.commit()
    delivery = await get_delivery_with_orders(delivery_id, session)
    if not delivery:
        await safe_answer_callback(callback, "Delivery not found", show_alert=True)
        return

    await safe_answer_callback(callback)

    delivery_orders = sorted(delivery.delivery_orders or [], key=lambda item: (item.sequence, item.id))
    if not delivery_orders:
        await _safe_edit_or_reply(
            callback,
            "No order assigned to this delivery yet. Please refresh from Delivery Hub.",
            reply_markup=_delivery_hub_keyboard(is_agent=True),
        )
        return

    await state.update_data(
        delivery_id=delivery_id,
        order_index=0,
        delivery_orders_data=_build_pickup_stops(delivery_orders),
    )
    await _show_next_seller_confirmation(callback, state, session)


@router.callback_query(F.data.startswith("delivery_arrived_"))
async def delivery_arrived_at_seller(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    """Agent confirms arrival at seller location, then uploads pickup proof photo."""

    delivery_order_id = _parse_callback_id(callback.data, "delivery_arrived_")
    if delivery_order_id is None:
        await safe_answer_callback(callback, "Invalid delivery order ID", show_alert=True)
        return

    if callback.from_user is None:
        await safe_answer_callback(callback, "Unable to identify delivery agent", show_alert=True)
        return
    agent = await get_agent_by_telegram_id(callback.from_user.id, session)
    if not agent:
        await safe_answer_callback(callback, "Agent profile not found", show_alert=True)
        return

    delivery_order = await session.get(DeliveryOrder, int(delivery_order_id))
    if not delivery_order:
        await safe_answer_callback(callback, "Delivery stop not found", show_alert=True)
        return
    delivery = await session.get(Delivery, int(delivery_order.delivery_id))
    if not delivery or delivery.agent_id != agent.id:
        await safe_answer_callback(callback, "This stop is not assigned to you", show_alert=True)
        return

    await state.update_data(
        delivery_id=int(delivery_order.delivery_id),
        current_delivery_order_id=int(delivery_order.id),
        current_order_id=int(delivery_order.order_id),
    )

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
    delivery_order_ids = [int(value) for value in (data.get("current_delivery_order_ids") or [])]
    current_order_id = data.get("current_order_id")
    order_index = int(data.get("order_index", 0))
    delivery_orders_data = data.get("delivery_orders_data", [])

    if delivery_id is None or delivery_order_id is None:
        await state.clear()
        await message.reply("Pickup session expired. Please restart from your delivery card.")
        return

    photo_file_id = message.photo[-1].file_id
    ids_to_update = delivery_order_ids or [int(delivery_order_id)]
    for do_id in ids_to_update:
        updated = await update_delivery_order_status(do_id, session, note=f"Pickup proof photo: {photo_file_id}")
        if not updated:
            await session.rollback()
            await message.reply("Could not update pickup state. Please retry.")
            return

    await session.commit()

    if not delivery_orders_data:
        delivery = await get_delivery_with_orders(int(delivery_id), session)
        delivery_orders = sorted(delivery.delivery_orders or [], key=lambda item: (item.sequence, item.id)) if delivery else []
        delivery_orders_data = _build_pickup_stops(delivery_orders)
        await state.update_data(delivery_orders_data=delivery_orders_data)

    if delivery_orders_data:
        for idx, delivery_order_data in enumerate(delivery_orders_data):
            do_ids = [int(value) for value in delivery_order_data.get("delivery_order_ids", [int(delivery_order_data["id"])])]
            if int(delivery_order_id) in do_ids:
                order_index = idx
                break

    next_index = order_index + 1
    await state.update_data(order_index=next_index)

    if next_index < len(delivery_orders_data):
        await state.set_state(None)
        await message.answer(
            "Pickup saved as done for this stop.\n\nProceed to next location now?",
            reply_markup=_post_pickup_next_keyboard(int(delivery_id), has_next=True),
        )
        return

    agent = None
    if message.from_user is not None:
        agent = await get_agent_by_telegram_id(message.from_user.id, session)
    await notify_buyer_all_pickups_completed(int(delivery_id), agent, session)

    await message.answer(
        "All items collected.\n\nReady to proceed with delivery?\n\n"
        + _pickup_checklist(delivery_orders_data, len(delivery_orders_data)),
        reply_markup=_delivery_progress_keyboard(int(delivery_id), stage="ready_in_transit"),
    )
    await state.clear()


@router.callback_query(F.data.regexp(r"^delivery_proceed_next_\d+$"))
async def delivery_proceed_next(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    match = re.match(r"^delivery_proceed_next_(\d+)$", callback.data or "")
    if not match:
        await safe_answer_callback(callback, "Invalid delivery selection", show_alert=True)
        return
    delivery_id = int(match.group(1))

    if callback.from_user is None:
        await safe_answer_callback(callback, "Unable to identify delivery agent", show_alert=True)
        return
    agent = await get_agent_by_telegram_id(callback.from_user.id, session)
    if not agent:
        await safe_answer_callback(callback, "Agent profile not found", show_alert=True)
        return

    delivery = await get_delivery_with_orders(delivery_id, session)
    if not delivery or delivery.agent_id != agent.id:
        await safe_answer_callback(callback, "This delivery is not assigned to you.", show_alert=True)
        return

    delivery_orders = sorted(delivery.delivery_orders or [], key=lambda item: (item.sequence, item.id))
    if not delivery_orders:
        await safe_answer_callback(callback, "No pickup stops found for this delivery.", show_alert=True)
        return

    grouped_stops = _build_pickup_stops(delivery_orders)
    next_index = 0
    picked_map = {int(item.id): item.picked_up_at is not None for item in delivery_orders}
    for idx, stop in enumerate(grouped_stops):
        stop_ids = [int(value) for value in stop.get("delivery_order_ids", [])]
        if any(not picked_map.get(do_id, False) for do_id in stop_ids):
            next_index = idx
            break
    else:
        await safe_answer_callback(callback)
        await _safe_edit_or_reply(
            callback,
            "All items for this delivery are already collected.",
            reply_markup=_delivery_progress_keyboard(delivery_id, stage="ready_in_transit"),
        )
        await state.clear()
        return

    await state.update_data(
        delivery_id=delivery_id,
        order_index=next_index,
        delivery_orders_data=grouped_stops,
    )
    await safe_answer_callback(callback)
    await _show_next_seller_confirmation(callback, state, session)


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

    await notify_buyer_group_delivery_status_update(delivery_id, DeliveryStatus.IN_TRANSIT.value, agent, session)

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
        (
            "<b>Update Location</b>\n\n"
            "Send your location manually as text.\n\n"
            "Examples:\n"
            "6.5244,3.3792\n"
            "6.5244,3.3792 | Near Main Gate\n"
            "Near Hall A, Block 2"
        ),
        parse_mode="HTML",
        reply_markup=_cancel_keyboard(),
    )
    await state.set_state(DeliveryAgentStates.awaiting_delivery_location)
    await state.update_data(delivery_id=delivery_id)


@router.message(DeliveryAgentStates.awaiting_delivery_location)
async def receive_delivery_location(message: Message, state: FSMContext, session: AsyncSession):
    """Agent sends manual location text; update delivery coordinates or note."""

    if not (message.text and message.text.strip()):
        await message.reply("Please type location text. Example: 6.5244,3.3792 | Near Main Gate")
        return

    data = await state.get_data()
    delivery_id = data.get("delivery_id")
    if delivery_id is None:
        await state.clear()
        await message.reply("Location session expired. Please restart from your delivery card.")
        return

    latitude, longitude, note_text = _parse_manual_location_input(message.text)
    if latitude is None and longitude is None and not note_text:
        await message.reply("Invalid location format. Example: 6.5244,3.3792 | Near Main Gate")
        return

    await update_delivery_status(
        int(delivery_id),
        DeliveryStatus.IN_TRANSIT,
        "AGENT",
        note_text or "Manual location update",
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

    await notify_buyer_group_delivery_status_update(delivery_id, DeliveryStatus.DELIVERED.value, agent, session)

    await state.clear()
    await _safe_edit_or_reply(
        callback,
        "<b>Delivery Completed</b>\n\nThank you for the update.",
        parse_mode="HTML",
        reply_markup=_delivery_hub_keyboard(is_agent=True),
    )


@router.callback_query(F.data == "delivery_cancel_workflow")
@router.callback_query(F.data.startswith("delivery_cancel_"))
async def delivery_cancel_workflow(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_answer_callback(callback, "Delivery workflow cancelled.")
    await _safe_edit_or_reply(
        callback,
        "Workflow cancelled. You can reopen any job from Delivery Hub.",
        reply_markup=_delivery_hub_keyboard(is_agent=True),
    )
