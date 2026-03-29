"""
Seller registration handler with FSM.
Handles both student and non-student seller registration.
"""

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.helpers.telegram import safe_answer_callback, safe_edit_text
from bot.keyboards.main_menu import get_confirmation_keyboard, get_main_menu_inline
from db.models import SellerProfile, User

router = Router()


class SellerRegistrationStates(StatesGroup):
    awaiting_student_choice = State()
    awaiting_student_email = State()
    awaiting_id_document = State()
    awaiting_bank_code = State()
    awaiting_account_number = State()
    awaiting_account_name = State()
    confirming_details = State()


@router.callback_query(F.data == "seller_register")
async def start_seller_registration(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    user_id = callback.from_user.id

    result = await session.execute(
        select(SellerProfile).join(User).where(User.telegram_id == str(user_id))
    )
    existing_seller = result.scalars().first()

    await safe_answer_callback(callback)

    if existing_seller:
        await safe_edit_text(
            callback,
            "You are already registered as a seller.\n\n"
            f"Status: {'Verified' if existing_seller.verified else 'Pending verification'}\n\n"
            "Go to 'My Listings' to manage your products.",
            reply_markup=get_main_menu_inline(),
        )
        return

    text = (
        "<b>Seller Registration</b>\n\n"
        "Are you a university student?\n\n"
        "Student sellers get priority visibility and lower fees."
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Yes, I am a student", callback_data="seller_student_yes")],
            [InlineKeyboardButton(text="No, I am not a student", callback_data="seller_student_no")],
            [InlineKeyboardButton(text="Cancel", callback_data="back_to_menu")],
        ]
    )

    await safe_edit_text(callback, text, parse_mode="HTML", reply_markup=keyboard)
    await state.set_state(SellerRegistrationStates.awaiting_student_choice)


@router.callback_query(F.data == "seller_student_yes", StateFilter(SellerRegistrationStates.awaiting_student_choice))
async def handle_student_yes(callback: CallbackQuery, state: FSMContext):
    await safe_answer_callback(callback)
    await state.update_data(is_student=True)

    await safe_edit_text(
        callback,
        "<b>Student Email</b>\n\n"
        "Please enter your university email address.\n"
        "Example: student@university.edu",
        parse_mode="HTML",
    )
    await state.set_state(SellerRegistrationStates.awaiting_student_email)


@router.callback_query(F.data == "seller_student_no", StateFilter(SellerRegistrationStates.awaiting_student_choice))
async def handle_student_no(callback: CallbackQuery, state: FSMContext):
    await safe_answer_callback(callback)
    await state.update_data(is_student=False)

    await safe_edit_text(
        callback,
        "<b>ID Document</b>\n\n"
        "Please send a photo of your ID document\n"
        "(National ID, Passport, Driver's License, etc.)",
        parse_mode="HTML",
    )
    await state.set_state(SellerRegistrationStates.awaiting_id_document)


@router.message(SellerRegistrationStates.awaiting_student_email)
async def handle_student_email(message: Message, state: FSMContext):
    email = message.text.strip()

    if "@" not in email or "." not in email:
        await message.reply("Please enter a valid email address.")
        return

    await state.update_data(student_email=email)

    await message.answer(
        "<b>ID Document</b>\n\n"
        "Please send a photo of your student ID or university ID card.",
        parse_mode="HTML",
    )
    await state.set_state(SellerRegistrationStates.awaiting_id_document)


@router.message(SellerRegistrationStates.awaiting_id_document, F.photo)
async def handle_id_document(message: Message, state: FSMContext):
    file_id = message.photo[-1].file_id
    await state.update_data(id_document_url=file_id)

    await message.answer(
        "<b>Bank Details</b>\n\n"
        "Enter your bank code.\n"
        "Example: 033 (First Bank), 044 (Access Bank), 050 (Ecobank)",
        parse_mode="HTML",
    )
    await state.set_state(SellerRegistrationStates.awaiting_bank_code)


@router.message(SellerRegistrationStates.awaiting_bank_code)
async def handle_bank_code(message: Message, state: FSMContext):
    bank_code = message.text.strip()

    if len(bank_code) < 2:
        await message.reply("Please enter a valid bank code.")
        return

    await state.update_data(bank_code=bank_code)

    await message.answer(
        "<b>Account Number</b>\n\n"
        "Enter your account number (10 digits).",
        parse_mode="HTML",
    )
    await state.set_state(SellerRegistrationStates.awaiting_account_number)


@router.message(SellerRegistrationStates.awaiting_account_number)
async def handle_account_number(message: Message, state: FSMContext):
    account_number = message.text.strip()

    if not account_number.isdigit() or len(account_number) < 8:
        await message.reply("Please enter a valid account number (8-10 digits).")
        return

    await state.update_data(account_number=account_number)

    await message.answer(
        "<b>Account Name</b>\n\n"
        "Enter the account holder name as it appears on your bank account.",
        parse_mode="HTML",
    )
    await state.set_state(SellerRegistrationStates.awaiting_account_name)


@router.message(SellerRegistrationStates.awaiting_account_name)
async def handle_account_name(message: Message, state: FSMContext):
    account_name = message.text.strip()
    data = await state.get_data()

    is_student = data.get("is_student", False)
    confirmation_text = (
        "<b>Confirm Your Details</b>\n\n"
        f"<b>Type:</b> {'Student' if is_student else 'Non-Student'}\n"
    )

    if is_student:
        confirmation_text += f"<b>Email:</b> {data.get('student_email')}\n"

    confirmation_text += (
        f"<b>Bank Code:</b> {data.get('bank_code')}\n"
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
            student_email=data.get("student_email"),
            id_document_url=data.get("id_document_url"),
            verified=False,
            bank_code=data.get("bank_code"),
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

        await safe_edit_text(callback, text, parse_mode="HTML", reply_markup=get_main_menu_inline())

    except Exception as e:
        await session.rollback()
        await safe_edit_text(callback, f"Error: {e}", reply_markup=get_main_menu_inline())

    await state.clear()


@router.callback_query(F.data == "confirm_no", StateFilter(SellerRegistrationStates.confirming_details))
async def reject_confirmation(callback: CallbackQuery, state: FSMContext):
    await safe_answer_callback(callback)
    await safe_edit_text(
        callback,
        "Edit your details from the beginning.",
        reply_markup=get_main_menu_inline(),
    )
    await state.clear()
