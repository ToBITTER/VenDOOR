"""
Seller registration handler with FSM.
Student-only seller registration flow.
"""

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.helpers.telegram import safe_answer_callback, safe_replace_with_screen
from bot.helpers.banks import bank_name_from_code, build_bank_picker_keyboard
from bot.helpers.residence import (
    build_floor_keyboard,
    build_hall_keyboard,
    build_room_keyboard,
    build_wing_keyboard,
    hall_from_index,
)
from bot.keyboards.main_menu import get_confirmation_keyboard, get_main_menu_inline
from core.config import get_settings
from db.models import SellerProfile, User
from services.korapay import get_korapay_client

router = Router()
settings = get_settings()


class SellerRegistrationStates(StatesGroup):
    awaiting_full_name = State()
    awaiting_level = State()
    awaiting_student_choice = State()
    awaiting_student_email = State()
    awaiting_hall = State()
    awaiting_wing = State()
    awaiting_floor = State()
    awaiting_room_number = State()
    awaiting_id_document = State()
    awaiting_bank_code = State()
    awaiting_account_number = State()
    awaiting_account_name_choice = State()
    awaiting_account_name = State()
    confirming_details = State()


def _account_name_choice_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Use Verified Name", callback_data="seller_account_name_accept")],
            [InlineKeyboardButton(text="Enter Manually", callback_data="seller_account_name_manual")],
        ]
    )


async def _send_registration_confirmation(message: Message, state: FSMContext, account_name: str) -> None:
    data = await state.get_data()
    bank_code = str(data.get("bank_code") or "").strip()
    bank_name = str(data.get("bank_name") or "").strip() or (bank_name_from_code(bank_code) or bank_code)

    confirmation_text = (
        "<b>Confirm Your Details</b>\n\n"
        f"<b>Full Name:</b> {data.get('full_name')}\n"
        f"<b>Level:</b> {data.get('level')}\n"
        "<b>Type:</b> Student\n"
        f"<b>Email:</b> {data.get('student_email')}\n"
        f"<b>Hall:</b> {data.get('hall')}\n"
        f"<b>Room Number:</b> {data.get('room_number')}\n"
        f"<b>Bank:</b> {bank_name}\n"
        f"<b>Bank Code:</b> {bank_code}\n"
        f"<b>Account Number:</b> {data.get('account_number')}\n"
        f"<b>Account Name:</b> {account_name}\n\n"
        "Is everything correct?"
    )

    await state.update_data(account_name=account_name)
    await message.answer(
        confirmation_text,
        parse_mode="HTML",
        reply_markup=get_confirmation_keyboard(),
    )
    await state.set_state(SellerRegistrationStates.confirming_details)


@router.callback_query(F.data == "seller_register")
async def start_seller_registration(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    user_id = callback.from_user.id

    result = await session.execute(
        select(SellerProfile).join(User).where(User.telegram_id == str(user_id))
    )
    existing_seller = result.scalars().first()

    await safe_answer_callback(callback)

    if existing_seller:
        await safe_replace_with_screen(
            callback,
            "You are already registered as a seller.\n\n"
            f"Status: {'Verified' if existing_seller.verified else 'Pending verification'}\n\n"
            "Go to 'My Listings' to manage your products.",
            reply_markup=get_main_menu_inline(),
        )
        return

    text = (
        "<b>Seller Registration</b>\n\n"
        "Seller onboarding is currently available for university students only.\n\n"
        "Continue to begin your student verification."
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Continue", callback_data="seller_student_yes")],
            [InlineKeyboardButton(text="Cancel", callback_data="back_to_menu")],
        ]
    )
    await safe_replace_with_screen(callback, text, parse_mode="HTML", reply_markup=keyboard)
    await state.set_state(SellerRegistrationStates.awaiting_student_choice)


@router.message(SellerRegistrationStates.awaiting_full_name)
async def handle_full_name(message: Message, state: FSMContext):
    full_name = (message.text or "").strip()
    if len(full_name.split()) < 2:
        await message.reply("Please enter your full name (first and last name).")
        return
    await state.update_data(full_name=full_name)
    await message.answer(
        "<b>Level</b>\n\nSend your level (e.g. 100L, 200L, 300L).",
        parse_mode="HTML",
    )
    await state.set_state(SellerRegistrationStates.awaiting_level)


@router.message(SellerRegistrationStates.awaiting_level)
async def handle_level(message: Message, state: FSMContext):
    level = (message.text or "").strip().upper()
    if not level:
        await message.reply("Please enter your level.")
        return
    await state.update_data(level=level)
    await message.answer(
        "<b>Student Email</b>\n\n"
        "Please enter your university email address.\n"
        "Example: example@stu.cu.edu.ng",
        parse_mode="HTML",
    )
    await state.set_state(SellerRegistrationStates.awaiting_student_email)


@router.callback_query(F.data == "seller_student_yes", StateFilter(SellerRegistrationStates.awaiting_student_choice))
async def handle_student_yes(callback: CallbackQuery, state: FSMContext):
    await safe_answer_callback(callback)
    await state.update_data(is_student=True, level=None, student_email=None, hall=None, room_number=None)

    await safe_replace_with_screen(
        callback,
        "<b>Seller Registration</b>\n\n"
        "Send your full name as it appears on your ID card.",
        parse_mode="HTML",
    )
    await state.set_state(SellerRegistrationStates.awaiting_full_name)


@router.callback_query(F.data == "seller_student_no", StateFilter(SellerRegistrationStates.awaiting_student_choice))
async def handle_student_no(callback: CallbackQuery, state: FSMContext):
    await safe_answer_callback(callback)
    await safe_replace_with_screen(
        callback,
        "Seller registration is currently available to students only.",
        reply_markup=get_main_menu_inline(),
        parse_mode="HTML",
    )
    await state.clear()


@router.message(SellerRegistrationStates.awaiting_student_email)
async def handle_student_email(message: Message, state: FSMContext):
    email = (message.text or "").strip().lower()
    if not email:
        await message.reply("Please enter your student email.")
        return

    if not email.endswith("@stu.cu.edu.ng"):
        await message.reply("Use your CU student email ending with @stu.cu.edu.ng.")
        return

    await state.update_data(student_email=email)

    await message.answer(
        "<b>Residence</b>\n\n"
        "Select your hall of residence:",
        parse_mode="HTML",
        reply_markup=build_hall_keyboard("seller_hall_"),
    )
    await state.set_state(SellerRegistrationStates.awaiting_hall)


@router.callback_query(
    F.data.startswith("seller_hall_"),
    StateFilter(SellerRegistrationStates.awaiting_hall),
)
async def handle_hall(callback: CallbackQuery, state: FSMContext):
    payload = (callback.data or "").replace("seller_hall_", "", 1).strip()
    if not payload.isdigit():
        await safe_answer_callback(callback, text="Invalid hall selection.", show_alert=True)
        return

    hall = hall_from_index(int(payload))
    if not hall:
        await safe_answer_callback(callback, text="Invalid hall selection.", show_alert=True)
        return

    await safe_answer_callback(callback)
    await state.update_data(hall=hall)
    await safe_replace_with_screen(
        callback,
        "<b>Residence</b>\n\nSelect your wing (A-H):",
        parse_mode="HTML",
        reply_markup=build_wing_keyboard("seller_wing_"),
    )
    await state.set_state(SellerRegistrationStates.awaiting_wing)


@router.callback_query(
    F.data.startswith("seller_wing_"),
    StateFilter(SellerRegistrationStates.awaiting_wing),
)
async def handle_wing(callback: CallbackQuery, state: FSMContext):
    wing = (callback.data or "").replace("seller_wing_", "", 1).strip().upper()
    if wing not in {"A", "B", "C", "D", "E", "F", "G", "H"}:
        await safe_answer_callback(callback, text="Invalid wing selection.", show_alert=True)
        return

    await safe_answer_callback(callback)
    await state.update_data(room_wing=wing)
    await safe_replace_with_screen(
        callback,
        "<b>Residence</b>\n\nSelect your floor:",
        parse_mode="HTML",
        reply_markup=build_floor_keyboard("seller_floor_"),
    )
    await state.set_state(SellerRegistrationStates.awaiting_floor)


@router.callback_query(
    F.data.startswith("seller_floor_"),
    StateFilter(SellerRegistrationStates.awaiting_floor),
)
async def handle_floor(callback: CallbackQuery, state: FSMContext):
    payload = (callback.data or "").replace("seller_floor_", "", 1).strip()
    if not payload.isdigit():
        await safe_answer_callback(callback, text="Invalid floor selection.", show_alert=True)
        return

    floor = int(payload)
    if floor not in {1, 2, 3, 4}:
        await safe_answer_callback(callback, text="Invalid floor selection.", show_alert=True)
        return

    await safe_answer_callback(callback)
    await state.update_data(room_floor=floor)
    await safe_replace_with_screen(
        callback,
        "<b>Residence</b>\n\nSelect your room number:",
        parse_mode="HTML",
        reply_markup=build_room_keyboard("seller_room_", floor),
    )
    await state.set_state(SellerRegistrationStates.awaiting_room_number)


@router.callback_query(
    F.data.startswith("seller_room_"),
    StateFilter(SellerRegistrationStates.awaiting_room_number),
)
async def handle_room_number(callback: CallbackQuery, state: FSMContext):
    payload = (callback.data or "").replace("seller_room_", "", 1).strip()
    if not payload.isdigit():
        await safe_answer_callback(callback, text="Invalid room selection.", show_alert=True)
        return

    room_number = int(payload)
    if room_number < 101 or room_number > 411:
        await safe_answer_callback(callback, text="Invalid room selection.", show_alert=True)
        return

    data = await state.get_data()
    floor = int(data.get("room_floor", 0))
    if room_number // 100 != floor:
        await safe_answer_callback(callback, text="Room does not match selected floor.", show_alert=True)
        return

    wing = str(data.get("room_wing") or "").upper()
    if wing not in {"A", "B", "C", "D", "E", "F", "G", "H"}:
        await safe_answer_callback(callback, text="Wing selection missing. Please restart residence step.", show_alert=True)
        return

    await safe_answer_callback(callback)
    await state.update_data(room_number=f"{wing} {room_number}")
    await safe_replace_with_screen(
        callback,
        "<b>ID Document</b>\n\nPlease send a photo of your student ID card.",
        parse_mode="HTML",
    )
    await state.set_state(SellerRegistrationStates.awaiting_id_document)


@router.message(SellerRegistrationStates.awaiting_id_document, F.photo)
async def handle_id_document(message: Message, state: FSMContext):
    file_id = message.photo[-1].file_id
    await state.update_data(id_document_url=file_id)
    await message.answer(
        "<b>Bank Details</b>\n\n"
        "Select your bank:",
        parse_mode="HTML",
        reply_markup=build_bank_picker_keyboard("seller_bank_"),
    )
    await state.set_state(SellerRegistrationStates.awaiting_bank_code)


@router.callback_query(F.data.startswith("seller_bank_"), StateFilter(SellerRegistrationStates.awaiting_bank_code))
async def handle_bank_picker(callback: CallbackQuery, state: FSMContext):
    bank_code = (callback.data or "").replace("seller_bank_", "", 1).strip()
    bank_name = bank_name_from_code(bank_code)
    if not bank_name:
        await safe_answer_callback(callback, text="Invalid bank selection.", show_alert=True)
        return

    await safe_answer_callback(callback)
    await state.update_data(bank_code=bank_code, bank_name=bank_name)
    await safe_replace_with_screen(
        callback,
        (
            "<b>Account Number</b>\n\n"
            f"Bank: {bank_name}\n"
            "Enter your account number (10 digits)."
        ),
        parse_mode="HTML",
    )
    await state.set_state(SellerRegistrationStates.awaiting_account_number)


@router.message(SellerRegistrationStates.awaiting_bank_code)
async def handle_bank_code(message: Message, state: FSMContext):
    bank_code = (message.text or "").strip()
    bank_name = bank_name_from_code(bank_code)

    if len(bank_code) < 2 and not bank_name:
        await message.reply("Please select your bank from the list.")
        return

    await state.update_data(bank_code=bank_code, bank_name=(bank_name or bank_code))

    await message.answer(
        "<b>Account Number</b>\n\n"
        "Enter your account number (10 digits).",
        parse_mode="HTML",
    )
    await state.set_state(SellerRegistrationStates.awaiting_account_number)


@router.message(SellerRegistrationStates.awaiting_account_number)
async def handle_account_number(message: Message, state: FSMContext):
    account_number = (message.text or "").strip()

    if not account_number.isdigit() or len(account_number) != 10:
        await message.reply("Please enter a valid account number (10 digits).")
        return

    data = await state.get_data()
    bank_code = str(data.get("bank_code") or "").strip()
    bank_name = str(data.get("bank_name") or "").strip() or (bank_name_from_code(bank_code) or bank_code)
    await state.update_data(account_number=account_number, bank_name=bank_name)

    korapay = get_korapay_client()
    resolution = await korapay.resolve_bank_account_name(bank_code=bank_code, account_number=account_number)
    if resolution.ok and resolution.account_name:
        await state.update_data(verified_account_name=resolution.account_name)
        await message.answer(
            (
                "<b>Account Verification</b>\n\n"
                f"Bank: {bank_name}\n"
                f"Resolved Account Name: <b>{resolution.account_name}</b>\n\n"
                "Use this verified account name?"
            ),
            parse_mode="HTML",
            reply_markup=_account_name_choice_keyboard(),
        )
        await state.set_state(SellerRegistrationStates.awaiting_account_name_choice)
        return

    await message.answer(
        "<b>Account Name</b>\n\n"
        "We could not auto-verify account name right now.\n"
        "Enter the account holder name as it appears on your bank account.",
        parse_mode="HTML",
    )
    await state.set_state(SellerRegistrationStates.awaiting_account_name)


@router.callback_query(
    F.data == "seller_account_name_accept",
    StateFilter(SellerRegistrationStates.awaiting_account_name_choice),
)
async def accept_verified_account_name(callback: CallbackQuery, state: FSMContext):
    await safe_answer_callback(callback)
    data = await state.get_data()
    verified_name = (data.get("verified_account_name") or "").strip()
    if not verified_name:
        await safe_replace_with_screen(
            callback,
            "Verified account name not found. Please enter manually.",
            parse_mode="HTML",
        )
        await state.set_state(SellerRegistrationStates.awaiting_account_name)
        return

    if callback.message:
        await _send_registration_confirmation(callback.message, state, verified_name)


@router.callback_query(
    F.data == "seller_account_name_manual",
    StateFilter(SellerRegistrationStates.awaiting_account_name_choice),
)
async def choose_manual_account_name(callback: CallbackQuery, state: FSMContext):
    await safe_answer_callback(callback)
    await safe_replace_with_screen(
        callback,
        "<b>Account Name</b>\n\nEnter the account holder name as it appears on your bank account.",
        parse_mode="HTML",
    )
    await state.set_state(SellerRegistrationStates.awaiting_account_name)


@router.message(SellerRegistrationStates.awaiting_account_name)
async def handle_account_name(message: Message, state: FSMContext):
    account_name = (message.text or "").strip()
    if len(account_name) < 2:
        await message.reply("Please enter a valid account name.")
        return
    await _send_registration_confirmation(message, state, account_name)


@router.callback_query(F.data == "confirm_yes", StateFilter(SellerRegistrationStates.confirming_details))
async def confirm_seller_registration(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    user_id = callback.from_user.id
    await safe_answer_callback(callback)

    try:
        result = await session.execute(select(User).where(User.telegram_id == str(user_id)))
        user = result.scalars().first()

        seller = SellerProfile(
            user_id=user.id,
            is_student=data.get("is_student", False),
            full_name=data.get("full_name"),
            level=data.get("level"),
            student_email=data.get("student_email"),
            hall=data.get("hall"),
            room_number=data.get("room_number"),
            address=data.get("address"),
            id_document_url=data.get("id_document_url"),
            verified=False,
            bank_code=data.get("bank_code"),
            bank_name=data.get("bank_name"),
            account_number=data.get("account_number"),
            account_name=data.get("account_name"),
        )
        session.add(seller)
        await session.commit()

        text = (
            "<b>Registration Complete</b>\n\n"
            "Your seller profile has been created.\n"
            "Our team will verify your details within 24 hours.\n\n"
            "We will notify you when verification is complete."
        )

        await safe_replace_with_screen(
            callback,
            text,
            parse_mode="HTML",
            reply_markup=get_main_menu_inline(),
        )

        if settings.admin_telegram_id:
            try:
                await callback.bot.send_message(
                    chat_id=int(settings.admin_telegram_id),
                    text=(
                        "New seller registration pending review.\n"
                        f"Seller ID: {seller.seller_code}\n"
                        "Use /pending_sellers to review."
                    ),
                )
            except Exception:
                pass

    except Exception as e:
        await session.rollback()
        await safe_replace_with_screen(callback, f"Error: {e}", reply_markup=get_main_menu_inline())

    await state.clear()


@router.callback_query(F.data == "confirm_no", StateFilter(SellerRegistrationStates.confirming_details))
async def reject_confirmation(callback: CallbackQuery, state: FSMContext):
    await safe_answer_callback(callback)
    await safe_replace_with_screen(
        callback,
        "Edit your details from the beginning.",
        reply_markup=get_main_menu_inline(),
    )
    await state.clear()
