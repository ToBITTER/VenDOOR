"""
Start handler for /start command.
Displays welcome message and main menu.
"""

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.helpers.brand_assets import get_help_banner, get_welcome_banner
from bot.helpers.telegram import safe_answer_callback, safe_edit_text
from bot.keyboards.main_menu import get_main_menu_inline
from db.models import User

router = Router()


WELCOME_TEXT = (
    "<b>Welcome to VenDOOR</b>\n"
    "buy. sell. secure.\n\n"
    "Your friendly campus marketplace for trusted buying and selling.\n\n"
    "What would you like to do today?"
)


@router.message(CommandStart())
async def start_handler(message: Message, session: AsyncSession):
    telegram_id = str(message.from_user.id)

    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalars().first()

    if not user:
        user = User(
            telegram_id=telegram_id,
            username=message.from_user.username,
            first_name=message.from_user.first_name or "User",
            last_name=message.from_user.last_name,
        )
        session.add(user)
        await session.commit()

    welcome_banner = get_welcome_banner()
    if welcome_banner:
        await message.answer_photo(
            photo=welcome_banner,
            caption=WELCOME_TEXT,
            parse_mode="HTML",
            reply_markup=get_main_menu_inline(),
        )
    else:
        await message.answer(
            WELCOME_TEXT,
            parse_mode="HTML",
            reply_markup=get_main_menu_inline(),
        )


@router.callback_query(F.data == "back_to_menu")
async def back_to_menu_handler(callback: CallbackQuery):
    await safe_answer_callback(callback)
    await safe_edit_text(
        callback,
        "<b>VenDOOR Main Menu</b>\n"
        "buy. sell. secure.\n\n"
        "What would you like to do today?",
        parse_mode="HTML",
        reply_markup=get_main_menu_inline(),
    )


@router.callback_query(F.data == "help")
async def help_handler(callback: CallbackQuery):
    help_text = (
        "<b>VenDOOR Help</b>\n"
        "buy. sell. secure.\n\n"
        "Here is how VenDOOR works:\n\n"
        "<b>Buying:</b>\n"
        "1. Browse catalog by category\n"
        "2. Select an item and pay\n"
        "3. Funds go to escrow\n"
        "4. Confirm receipt when you get the item\n\n"
        "<b>Selling:</b>\n"
        "1. Register as a seller\n"
        "2. Verify your identity (student or non-student)\n"
        "3. Create listings with product photos\n"
        "4. Get paid when buyers confirm receipt\n\n"
        "<b>Escrow:</b>\n"
        "Your payment is protected. Seller payment is released after receipt confirmation.\n"
        "After 48 hours with no dispute, funds auto-release to seller.\n\n"
        "<b>Need support?</b>\n"
        "Raise a complaint from your orders and our team will review it."
    )

    await safe_answer_callback(callback)
    help_banner = get_help_banner()
    if help_banner:
        await callback.message.answer_photo(
            photo=help_banner,
            caption=help_text,
            parse_mode="HTML",
            reply_markup=get_main_menu_inline(),
        )
    else:
        await safe_edit_text(callback, help_text, parse_mode="HTML", reply_markup=get_main_menu_inline())
