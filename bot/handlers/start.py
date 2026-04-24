"""
Start handler for /start command.
Displays welcome message and main menu.
"""

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
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
    MENU_TERMS,
    get_main_menu_inline,
)
from db.models import User

router = Router()


class _MenuProxyCallback:
    def __init__(self, message: Message, data: str):
        self.message = message
        self.from_user = message.from_user
        self.data = data
        self.bot = message.bot

    async def answer(self, *args, **kwargs):
        return True


WELCOME_TEXT = (
    "<b>Welcome to VenDOOR</b>\n"
    "buy. sell. secure.\n\n"
    "Your friendly campus marketplace for trusted buying and selling.\n\n"
    "What would you like to do today?"
)


HELP_TEXT = (
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

    # Force-clear legacy reply keyboards from older bot versions.
    await message.answer("Main menu refreshed.", reply_markup=ReplyKeyboardRemove())

    welcome_banner = get_welcome_banner()
    if welcome_banner:
        await message.answer_photo(
            photo=welcome_banner,
            caption="<b>Welcome to VenDOOR</b>",
            parse_mode="HTML",
            reply_markup=get_main_menu_inline(),
        )
    else:
        await message.answer(
            WELCOME_TEXT,
            parse_mode="HTML",
            reply_markup=get_main_menu_inline(),
        )


@router.message(Command("menu"))
async def menu_command_handler(message: Message):
    # Remove stale reply keyboards from older bot states and render inline menu.
    await message.answer("Main menu refreshed.", reply_markup=ReplyKeyboardRemove())
    menu_text = "<b>VenDOOR</b>"
    main_menu_banner = get_main_menu_banner()
    if main_menu_banner:
        await message.answer_photo(
            photo=main_menu_banner,
            caption=menu_text,
            parse_mode="HTML",
            reply_markup=get_main_menu_inline(),
        )
    else:
        await message.answer(
            menu_text,
            parse_mode="HTML",
            reply_markup=get_main_menu_inline(),
        )


@router.message(Command("help"))
async def help_command_handler(message: Message):
    await message.answer(
        HELP_TEXT,
        parse_mode="HTML",
        reply_markup=get_main_menu_inline(),
    )


@router.callback_query(F.data == "back_to_menu")
async def back_to_menu_handler(callback: CallbackQuery):
    await safe_answer_callback(callback)
    menu_text = "<b>VenDOOR</b>"
    main_menu_banner = get_main_menu_banner()
    await safe_replace_with_screen(
        callback,
        menu_text,
        photo=main_menu_banner,
        parse_mode="HTML",
        reply_markup=get_main_menu_inline(),
    )


@router.callback_query(F.data == "help")
async def help_handler(callback: CallbackQuery):
    await safe_answer_callback(callback)
    help_banner = get_help_banner()
    await safe_replace_with_screen(
        callback,
        HELP_TEXT,
        photo=help_banner,
        parse_mode="HTML",
        reply_markup=get_main_menu_inline(),
    )


@router.callback_query(F.data == "terms_conditions")
async def terms_conditions_handler(callback: CallbackQuery):
    terms_text = (
        "<b>Terms & Conditions</b>\n\n"
        "<b>For Buyers</b>\n"
        "1. Pay only through VenDOOR checkout.\n"
        "2. Confirm receipt only after delivery.\n"
        "3. Raise disputes promptly for unresolved issues.\n\n"
        "<b>For Sellers</b>\n"
        "1. Listings must be accurate and lawful.\n"
        "2. Sellers must fulfill paid orders promptly.\n"
        "3. Payout is released after delivery confirmation/auto-release.\n\n"
        "<b>General</b>\n"
        "1. Fraud, abuse, or policy violations can lead to account suspension.\n"
        "2. VenDOOR may update policies as the platform evolves."
    )
    await safe_answer_callback(callback)
    await safe_replace_with_screen(
        callback,
        terms_text,
        parse_mode="HTML",
        reply_markup=get_main_menu_inline(),
    )


@router.message(F.text.in_([MENU_BROWSE, MENU_CART, MENU_ORDERS, MENU_SELLER, MENU_LISTINGS, MENU_COMPLAINTS, MENU_HELP, MENU_DELIVERY, MENU_TERMS]))
async def quick_menu_router(message: Message, session: AsyncSession, state: FSMContext):
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
        MENU_TERMS: "terms_conditions",
    }
    callback_data = callback_map.get(text)
    if not callback_data:
        return

    if callback_data == "browse_catalog":
        from bot.keyboards.main_menu import get_catalog_categories

        catalog_text = "<b>Browse Categories</b>\nChoose where you want to shop:"
        welcome_banner = get_welcome_banner()
        if welcome_banner:
            await message.answer_photo(
                photo=welcome_banner,
                caption=catalog_text,
                parse_mode="HTML",
                reply_markup=get_catalog_categories(),
            )
        else:
            await message.answer(
                catalog_text,
                parse_mode="HTML",
                reply_markup=get_catalog_categories(),
            )
        return

    proxy = _MenuProxyCallback(message, callback_data)
    if callback_data == "my_orders":
        from bot.handlers.buyer import orders as buyer_orders

        await buyer_orders.my_orders(proxy, session)
        return
    if callback_data == "my_cart":
        from bot.handlers.buyer import cart as buyer_cart

        await buyer_cart.my_cart(proxy, session)
        return
    if callback_data == "seller_register":
        from bot.handlers.seller import register as seller_register

        await seller_register.start_seller_registration(proxy, state, session)
        return
    if callback_data == "seller_listings":
        from bot.handlers.seller import listings as seller_listings

        await seller_listings.view_seller_listings(proxy, session)
        return
    if callback_data == "complaints":
        from bot.handlers import complaints as complaint_handlers

        await complaint_handlers.start_complaint(proxy, state, session)
        return
    if callback_data == "help":
        await help_handler(proxy)
        return
    if callback_data == "delivery_hub":
        from bot.handlers.seller import delivery_agent as delivery_agent_handlers

        await delivery_agent_handlers.delivery_hub(proxy, session)
        return
    if callback_data == "terms_conditions":
        await terms_conditions_handler(proxy)
        return
