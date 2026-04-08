"""
Start handler for /start command.
Displays welcome message and main menu.
"""

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.helpers.brand_assets import get_help_banner, get_main_menu_banner, get_welcome_banner
from bot.helpers.telegram import (
    safe_answer_callback,
    safe_edit_text,
    safe_replace_with_screen,
)
from bot.keyboards.main_menu import (
    MENU_BROWSE,
    MENU_CART,
    MENU_COMPLAINTS,
    MENU_DELIVERY,
    MENU_HELP,
    MENU_LISTINGS,
    MENU_ORDERS,
    MENU_SELLER,
    get_main_menu_inline,
    get_main_menu_reply,
)
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
            reply_markup=get_main_menu_reply(),
        )
    else:
        await message.answer(
            WELCOME_TEXT,
            parse_mode="HTML",
            reply_markup=get_main_menu_reply(),
        )

    await message.answer(
        "Quick access menu is active below. You can also tap inline buttons on screens.",
        reply_markup=get_main_menu_reply(),
    )


@router.callback_query(F.data == "back_to_menu")
async def back_to_menu_handler(callback: CallbackQuery):
    await safe_answer_callback(callback)
    menu_text = (
        "<b>VenDOOR Main Menu</b>\n"
        "buy. sell. secure.\n\n"
        "What would you like to do today?"
    )
    main_menu_banner = get_main_menu_banner()
    await safe_replace_with_screen(
        callback,
        menu_text,
        photo=main_menu_banner,
        parse_mode="HTML",
        reply_markup=get_main_menu_inline(),
    )
    if callback.message:
        await callback.message.answer("Main menu ready.", reply_markup=get_main_menu_reply())


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
        "Your payment is protected. Seller payment is released after receipt confirmation.\n\n"
        "<b>Need support?</b>\n"
        "Raise a complaint from your orders and our team will review it."
    )

    await safe_answer_callback(callback)
    help_banner = get_help_banner()
    await safe_replace_with_screen(
        callback,
        help_text,
        photo=help_banner,
        parse_mode="HTML",
        reply_markup=get_main_menu_inline(),
    )
    if callback.message:
        await callback.message.answer("You can always use the quick menu below.", reply_markup=get_main_menu_reply())


@router.message(F.text.in_([MENU_BROWSE, MENU_CART, MENU_ORDERS, MENU_SELLER, MENU_LISTINGS, MENU_COMPLAINTS, MENU_HELP, MENU_DELIVERY]))
async def quick_menu_router(message: Message):
    text = (message.text or "").strip()
    callback_map = {
        MENU_BROWSE: "browse_catalog",
        MENU_CART: "my_cart",
        MENU_ORDERS: "my_orders",
        MENU_SELLER: "seller_register",
        MENU_LISTINGS: "seller_listings",
        MENU_COMPLAINTS: "complaints",
        MENU_HELP: "help",
        MENU_DELIVERY: "delivery_hub",
    }
    callback_data = callback_map.get(text)
    if not callback_data:
        return

    await message.answer(
        f"Opening {text}...",
        reply_markup=get_main_menu_reply(),
    )
    if callback_data == "browse_catalog":
        from bot.keyboards.main_menu import get_catalog_categories

        await message.answer(
            "<b>Browse Categories</b>\nChoose where you want to shop:",
            parse_mode="HTML",
            reply_markup=get_catalog_categories(),
        )
    else:
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

        await message.answer(
            "Tap below to continue:",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=text, callback_data=callback_data)]]
            ),
        )
