"""
Complaints and disputes handler FSM.
Allows buyers and sellers to raise disputes about orders.
"""

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import false, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.helpers.brand_assets import get_empty_state
from bot.helpers.telegram import safe_answer_callback, safe_replace_with_screen
from bot.keyboards.main_menu import get_main_menu_inline
from db.models import Complaint, DisputeStatus, Order, OrderStatus, SellerProfile, User

router = Router()


def _callback_int_suffix(callback_data: str | None, prefix: str) -> int | None:
    payload = (callback_data or "").strip()
    if not payload.startswith(prefix):
        return None
    value = payload.replace(prefix, "", 1).strip()
    if not value.isdigit():
        return None
    return int(value)


class ComplaintStates(StatesGroup):
    awaiting_order_selection = State()
    awaiting_subject = State()
    awaiting_description = State()
    awaiting_evidence = State()
    confirming_complaint = State()


@router.callback_query(F.data == "complaints")
async def start_complaint(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    user_id_str = str(callback.from_user.id)

    result = await session.execute(select(User).where(User.telegram_id == user_id_str))
    user = result.scalars().first()
    if not user:
        await safe_answer_callback(callback, text="User not found", show_alert=True)
        return

    await safe_answer_callback(callback)

    seller_profile_result = await session.execute(
        select(SellerProfile).where(SellerProfile.user_id == user.id)
    )
    seller_profile = seller_profile_result.scalars().first()

    seller_match = Order.seller_id == seller_profile.id if seller_profile else false()
    result = await session.execute(
        select(Order)
        .options(selectinload(Order.listing))
        .where((Order.buyer_id == user.id) | seller_match)
        .where(Order.status.in_([OrderStatus.PAID, OrderStatus.COMPLETED]))
        .order_by(Order.created_at.desc())
    )
    orders = result.scalars().all()

    if not orders:
        empty_image = get_empty_state("no_complaints")
        empty_text = (
            "No orders available to file complaints.\n\n"
            "Only orders in PAID or COMPLETED status can be disputed."
        )
        await safe_replace_with_screen(
            callback,
            empty_text,
            photo=empty_image,
            reply_markup=get_main_menu_inline(),
        )
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Order #{order.id} - {order.listing.title[:20]}",
                    callback_data=f"complaint_order_{order.id}",
                )
            ]
            for order in orders[:5]
        ]
        + [[InlineKeyboardButton(text="Cancel", callback_data="back_to_menu")]]
    )

    await safe_replace_with_screen(
        callback,
        "<b>File a Complaint</b>\n\nSelect an order to file a complaint about:",
        parse_mode="HTML",
        reply_markup=keyboard,
    )
    await state.set_state(ComplaintStates.awaiting_order_selection)


@router.callback_query(F.data.startswith("order_dispute_"))
async def start_complaint_from_order(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    order_id = _callback_int_suffix(callback.data, "order_dispute_")
    if order_id is None:
        await safe_answer_callback(callback, text="Invalid order selection", show_alert=True)
        return

    user_result = await session.execute(select(User).where(User.telegram_id == str(callback.from_user.id)))
    user = user_result.scalars().first()
    if not user:
        await safe_answer_callback(callback, text="User not found", show_alert=True)
        return

    order_result = await session.execute(
        select(Order)
        .options(selectinload(Order.listing))
        .where(Order.id == order_id)
    )
    order = order_result.scalars().first()
    if not order:
        await safe_answer_callback(callback, text="Order not found", show_alert=True)
        return

    seller_profile_result = await session.execute(select(SellerProfile).where(SellerProfile.user_id == user.id))
    seller_profile = seller_profile_result.scalars().first()
    seller_id = seller_profile.id if seller_profile else None

    if order.buyer_id != user.id and order.seller_id != seller_id:
        await safe_answer_callback(callback, text="You can only dispute your own orders", show_alert=True)
        return

    if order.status not in [OrderStatus.PAID, OrderStatus.COMPLETED]:
        await safe_answer_callback(
            callback,
            text="Only PAID or COMPLETED orders can be disputed.",
            show_alert=True,
        )
        return

    existing_complaint_result = await session.execute(
        select(Complaint.id).where(Complaint.order_id == order.id)
    )
    existing_complaint_id = existing_complaint_result.scalar_one_or_none()
    if existing_complaint_id is not None:
        await safe_answer_callback(
            callback,
            text=f"Complaint already exists for this order (#{existing_complaint_id}).",
            show_alert=True,
        )
        return

    await safe_answer_callback(callback)
    await state.update_data(order_id=order.id)
    await safe_replace_with_screen(
        callback,
        f"Order #{order.id} - {order.listing.title if order.listing else 'Unknown item'}\n\n"
        "What is the issue?\n\n"
        "Examples:\n"
        "- Non-receipt of item\n"
        "- Item does not match description\n"
        "- Item is damaged\n"
        "- Seller is unresponsive\n\n"
        "Please describe the issue:",
        parse_mode="HTML",
    )
    await state.set_state(ComplaintStates.awaiting_subject)


@router.callback_query(F.data.startswith("complaint_order_"), StateFilter(ComplaintStates.awaiting_order_selection))
async def select_complaint_order(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    order_id = _callback_int_suffix(callback.data, "complaint_order_")
    if order_id is None:
        await safe_answer_callback(callback, text="Invalid order selection", show_alert=True)
        return

    result = await session.execute(
        select(Order).options(selectinload(Order.listing)).where(Order.id == order_id)
    )
    order = result.scalars().first()

    if not order:
        await safe_answer_callback(callback, text="Order not found", show_alert=True)
        return

    await safe_answer_callback(callback)
    await state.update_data(order_id=order_id)

    await safe_replace_with_screen(
        callback,
        f"Order #{order.id} - {order.listing.title}\n\n"
        "What is the issue?\n\n"
        "Examples:\n"
        "- Non-receipt of item\n"
        "- Item does not match description\n"
        "- Item is damaged\n"
        "- Seller is unresponsive\n\n"
        "Please describe the issue:",
        parse_mode="HTML",
    )
    await state.set_state(ComplaintStates.awaiting_subject)


@router.message(ComplaintStates.awaiting_subject)
async def handle_complaint_subject(message: Message, state: FSMContext):
    subject = (message.text or "").strip()

    if len(subject) < 5:
        await message.reply("Please provide a subject with at least 5 characters.")
        return

    await state.update_data(subject=subject)

    await message.answer(
        "<b>Complaint Details</b>\n\n"
        "Please describe the full details of your complaint.\n"
        "Include what happened, when, and how it affected you.",
        parse_mode="HTML",
    )
    await state.set_state(ComplaintStates.awaiting_description)


@router.message(ComplaintStates.awaiting_description)
async def handle_complaint_description(message: Message, state: FSMContext):
    description = (message.text or "").strip()

    if len(description) < 10:
        await message.reply("Please provide more details (at least 10 characters).")
        return

    await state.update_data(description=description)

    await message.answer(
        "<b>Evidence (Optional)</b>\n\n"
        "Do you have any photos or evidence?\n"
        "Send a photo, or type 'Skip' if not.",
        parse_mode="HTML",
    )
    await state.set_state(ComplaintStates.awaiting_evidence)


@router.message(ComplaintStates.awaiting_evidence, F.photo)
async def handle_complaint_evidence_photo(message: Message, state: FSMContext):
    file_id = message.photo[-1].file_id
    await state.update_data(evidence_url=file_id)
    await show_complaint_confirmation(message, state)


@router.message(ComplaintStates.awaiting_evidence)
async def handle_complaint_evidence_skip(message: Message, state: FSMContext):
    text = (message.text or "").strip().lower()
    if text in ["skip", "no evidence", "-"]:
        await show_complaint_confirmation(message, state)
    else:
        await message.reply("Please send a photo or type 'Skip'.")


async def show_complaint_confirmation(message: Message, state: FSMContext):
    data = await state.get_data()

    text = (
        "<b>Complaint Summary</b>\n\n"
        f"<b>Order:</b> #{data.get('order_id')}\n"
        f"<b>Subject:</b> {data.get('subject')}\n\n"
        f"<b>Details:</b>\n{data.get('description')}\n\n"
        f"<b>Evidence:</b> {'Attached' if data.get('evidence_url') else 'None'}\n\n"
        "Submit this complaint?"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Submit", callback_data="complaint_submit"),
                InlineKeyboardButton(text="Cancel", callback_data="back_to_menu"),
            ]
        ]
    )

    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)
    await state.set_state(ComplaintStates.confirming_complaint)


@router.callback_query(F.data == "complaint_submit", StateFilter(ComplaintStates.confirming_complaint))
async def submit_complaint(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    user_id_str = str(callback.from_user.id)
    await safe_answer_callback(callback)

    try:
        result = await session.execute(select(User).where(User.telegram_id == user_id_str))
        user = result.scalars().first()
        if not user:
            await safe_replace_with_screen(
                callback,
                "User not found. Please send /start.",
                reply_markup=get_main_menu_inline(),
            )
            await state.clear()
            return

        complaint = Complaint(
            order_id=data.get("order_id"),
            complainant_id=user.id,
            subject=data.get("subject"),
            description=data.get("description"),
            evidence_url=data.get("evidence_url"),
            status=DisputeStatus.OPEN,
        )
        session.add(complaint)
        await session.commit()

        await safe_replace_with_screen(
            callback,
            "<b>Complaint Submitted</b>\n\n"
            "Your complaint has been recorded.\n"
            "Admin will review it within 24 hours.\n"
            "We will notify you about the resolution.\n\n"
            f"Complaint ID: {complaint.id}",
            parse_mode="HTML",
            reply_markup=get_main_menu_inline(),
        )

    except Exception:
        await session.rollback()
        await safe_replace_with_screen(
            callback,
            "Could not submit complaint right now. Please try again.",
            reply_markup=get_main_menu_inline(),
        )

    await state.clear()
