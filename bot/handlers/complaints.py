"""
Complaints and disputes handler FSM.
Allows buyers and sellers to raise disputes about orders.
"""

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select, false
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Order, User, Complaint, DisputeStatus, OrderStatus, SellerProfile
from bot.keyboards.main_menu import get_main_menu_inline

router = Router()


class ComplaintStates(StatesGroup):
    """FSM states for complaint filing."""
    awaiting_order_selection = State()
    awaiting_subject = State()
    awaiting_description = State()
    awaiting_evidence = State()
    confirming_complaint = State()


@router.callback_query(F.data == "complaints")
async def start_complaint(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    """
    Start complaint filing - select order.
    """
    user_id_str = str(callback.from_user.id)
    
    # Get user
    result = await session.execute(
        select(User).where(User.telegram_id == user_id_str)
    )
    user = result.scalars().first()
    
    if not user:
        await callback.answer("User not found", show_alert=True)
        return
    
    # Find seller profile (if user is also a seller)
    seller_profile_result = await session.execute(
        select(SellerProfile).where(SellerProfile.user_id == user.id)
    )
    seller_profile = seller_profile_result.scalars().first()

    # Get applicable orders (PAID or COMPLETED)
    seller_match = Order.seller_id == seller_profile.id if seller_profile else false()
    result = await session.execute(
        select(Order)
        .where((Order.buyer_id == user.id) | seller_match)
        .where(Order.status.in_([OrderStatus.PAID, OrderStatus.COMPLETED]))
        .order_by(Order.created_at.desc())
    )
    orders = result.scalars().all()
    
    if not orders:
        await callback.message.edit_text(
            "📭 No orders available to file complaints.\n\n"
            "Only orders in PAID or COMPLETED status can be disputed.",
            reply_markup=get_main_menu_inline(),
        )
        await callback.answer()
        return
    
    # Build inline buttons for order selection
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text=f"Order #{order.id} - {order.listing.title[:20]}",
                callback_data=f"complaint_order_{order.id}"
            )]
            for order in orders[:5]
        ] + [
            [InlineKeyboardButton(text="◀️ Cancel", callback_data="back_to_menu")]
        ]
    )
    
    await callback.message.edit_text(
        "⚠️ <b>File a Complaint</b>\n\n"
        "Select an order to file a complaint about:",
        parse_mode="HTML",
        reply_markup=keyboard,
    )
    await state.set_state(ComplaintStates.awaiting_order_selection)
    await callback.answer()


@router.callback_query(F.data.startswith("complaint_order_"), StateFilter(ComplaintStates.awaiting_order_selection))
async def select_complaint_order(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    """
    Order selected - ask for complaint subject.
    """
    order_id = int(callback.data.replace("complaint_order_", ""))
    
    result = await session.execute(
        select(Order).where(Order.id == order_id)
    )
    order = result.scalars().first()
    
    if not order:
        await callback.answer("Order not found", show_alert=True)
        return
    
    await state.update_data(order_id=order_id)
    
    await callback.message.edit_text(
        f"Order #{order.id} - {order.listing.title}\n\n"
        f"What's the issue?\n\n"
        f"Examples:\n"
        f"- Non-receipt of item\n"
        f"- Item doesn't match description\n"
        f"- Item is damaged\n"
        f"- Seller being unresponsive\n\n"
        f"Please describe the issue:",
        parse_mode="HTML",
    )
    await state.set_state(ComplaintStates.awaiting_subject)
    await callback.answer()


@router.message(ComplaintStates.awaiting_subject)
async def handle_complaint_subject(message: Message, state: FSMContext):
    """Collect complaint subject (brief)."""
    subject = message.text.strip()
    
    if len(subject) < 5:
        await message.reply("❌ Please provide a subject with at least 5 characters.")
        return
    
    await state.update_data(subject=subject)
    
    await message.answer(
        "📝 <b>Complaint Details</b>\n\n"
        "Please describe the full details of your complaint.\n"
        "Include what happened, when, and how it affected you.",
        parse_mode="HTML",
    )
    await state.set_state(ComplaintStates.awaiting_description)


@router.message(ComplaintStates.awaiting_description)
async def handle_complaint_description(message: Message, state: FSMContext):
    """Collect full complaint description."""
    description = message.text.strip()
    
    if len(description) < 10:
        await message.reply("❌ Please provide more details (at least 10 characters).")
        return
    
    await state.update_data(description=description)
    
    await message.answer(
        "📸 <b>Evidence (Optional)</b>\n\n"
        "Do you have any photos or evidence?\n"
        "(Send a photo, or type 'Skip' if not)",
        parse_mode="HTML",
    )
    await state.set_state(ComplaintStates.awaiting_evidence)


@router.message(ComplaintStates.awaiting_evidence, F.photo)
async def handle_complaint_evidence_photo(message: Message, state: FSMContext):
    """Collect evidence photo."""
    file_id = message.photo[-1].file_id
    await state.update_data(evidence_url=file_id)
    
    # Show confirmation
    await show_complaint_confirmation(message, state)


@router.message(ComplaintStates.awaiting_evidence)
async def handle_complaint_evidence_skip(message: Message, state: FSMContext):
    """Skip evidence or type 'Skip'."""
    if message.text.lower() in ["skip", "no evidence", "-"]:
        # Show confirmation
        await show_complaint_confirmation(message, state)
    else:
        await message.reply("❌ Please send a photo or type 'Skip'")


async def show_complaint_confirmation(message: Message, state: FSMContext):
    """Show complaint confirmation."""
    data = await state.get_data()
    
    text = (
        f"✅ <b>Complaint Summary</b>\n\n"
        f"<b>Order:</b> #{data.get('order_id')}\n"
        f"<b>Subject:</b> {data.get('subject')}\n\n"
        f"<b>Details:</b>\n{data.get('description')}\n\n"
        f"<b>Evidence:</b> {'✅ Attached' if data.get('evidence_url') else '❌ None'}\n\n"
        f"Submit this complaint?"
    )
    
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Submit", callback_data="complaint_submit"),
                InlineKeyboardButton(text="❌ Cancel", callback_data="back_to_menu"),
            ]
        ]
    )
    
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)
    await state.set_state(ComplaintStates.confirming_complaint)


@router.callback_query(F.data == "complaint_submit", StateFilter(ComplaintStates.confirming_complaint))
async def submit_complaint(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    """
    Submit complaint to database.
    """
    data = await state.get_data()
    user_id_str = str(callback.from_user.id)
    
    try:
        # Get user
        result = await session.execute(
            select(User).where(User.telegram_id == user_id_str)
        )
        user = result.scalars().first()
        
        # Create complaint
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
        
        await callback.message.edit_text(
            f"🎉 <b>Complaint Submitted!</b>\n\n"
            f"✅ Your complaint has been recorded.\n"
            f"📞 Admin will review it within 24 hours.\n"
            f"📧 We'll notify you about the resolution.\n\n"
            f"Complaint ID: {complaint.id}",
            parse_mode="HTML",
            reply_markup=get_main_menu_inline(),
        )
        
    except Exception as e:
        await session.rollback()
        await callback.message.edit_text(
            f"❌ Error: {str(e)}",
            reply_markup=get_main_menu_inline(),
        )
    
    await state.clear()
    await callback.answer()
