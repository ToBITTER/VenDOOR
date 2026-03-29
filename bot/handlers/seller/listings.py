"""
Seller listings handler - create and view listings.
"""

from decimal import Decimal, ROUND_CEILING

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.helpers.telegram import safe_answer_callback, safe_edit_text
from bot.keyboards.main_menu import get_main_menu_inline, get_seller_actions
from db.models import Category, Listing, SellerProfile, User

router = Router()


class ListingStates(StatesGroup):
    awaiting_title = State()
    awaiting_description = State()
    awaiting_category = State()
    awaiting_base_price = State()
    confirming_listing = State()


@router.callback_query(F.data == "seller_listings")
async def view_seller_listings(callback: CallbackQuery, session: AsyncSession):
    await safe_answer_callback(callback)
    user_id_str = str(callback.from_user.id)
    result = await session.execute(select(User).where(User.telegram_id == user_id_str))
    user = result.scalars().first()
    if not user:
        await safe_answer_callback(callback, text="User not found", show_alert=True)
        return

    result = await session.execute(select(SellerProfile).where(SellerProfile.user_id == user.id))
    seller = result.scalars().first()
    if not seller:
        await safe_edit_text(
            callback,
            "You are not registered as a seller.\n\nRegister now to start selling.",
            reply_markup=get_main_menu_inline(),
        )
        return

    result = await session.execute(
        select(Listing).where(Listing.seller_id == seller.id).order_by(Listing.created_at.desc())
    )
    listings = result.scalars().all()
    if not listings:
        await safe_edit_text(
            callback,
            "You have not created any listings yet.\n\nCreate your first listing now.",
            reply_markup=get_seller_actions(),
        )
        return

    text = "<b>My Listings</b>\n\n"
    for listing in listings[:10]:
        status = "Active" if listing.available else "Inactive"
        text += (
            f"<b>{listing.title}</b>\n"
            f"Price: NGN {listing.buyer_price:,.2f}\n"
            f"Status: {status}\n"
            f"Category: {listing.category.value}\n\n"
        )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Create New Listing", callback_data="seller_create_listing")],
            [InlineKeyboardButton(text="Back", callback_data="back_to_menu")],
        ]
    )
    await safe_edit_text(callback, text, parse_mode="HTML", reply_markup=keyboard)


@router.callback_query(F.data == "seller_create_listing")
async def start_create_listing(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    await safe_answer_callback(callback)
    user_id_str = str(callback.from_user.id)
    result = await session.execute(
        select(SellerProfile).join(User).where(User.telegram_id == user_id_str)
    )
    seller = result.scalars().first()

    if not seller:
        await safe_edit_text(
            callback,
            "You must be registered as a seller first.",
            reply_markup=get_main_menu_inline(),
        )
        return

    if not seller.verified:
        await safe_edit_text(
            callback,
            "Your seller account is still pending verification.\n\n"
            "You can create listings after verification.",
            reply_markup=get_seller_actions(),
        )
        return

    await state.update_data(seller_id=seller.id)
    await safe_edit_text(
        callback,
        "<b>Create New Listing</b>\n\nWhat is the product title?",
        parse_mode="HTML",
    )
    await state.set_state(ListingStates.awaiting_title)


@router.message(ListingStates.awaiting_title)
async def handle_listing_title(message: Message, state: FSMContext):
    title = message.text.strip()
    if len(title) < 5:
        await message.reply("Title must be at least 5 characters.")
        return
    await state.update_data(title=title)
    await message.answer(
        "<b>Product Description</b>\n\nDescribe the product. Max 500 characters.",
        parse_mode="HTML",
    )
    await state.set_state(ListingStates.awaiting_description)


@router.message(ListingStates.awaiting_description)
async def handle_listing_description(message: Message, state: FSMContext):
    description = message.text.strip()
    if len(description) < 10 or len(description) > 500:
        await message.reply("Description must be 10-500 characters.")
        return
    await state.update_data(description=description)

    categories = [
        ("iPads", "cat_IPADS"),
        ("iPods", "cat_IPODS"),
        ("Jewelry", "cat_JEWELRY"),
        ("Clothes", "cat_CLOTHES"),
        ("Electronics", "cat_ELECTRONICS"),
        ("Books", "cat_BOOKS"),
        ("Shoes", "cat_SHOES"),
        ("Others", "cat_OTHERS"),
    ]
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=cb)] for label, cb in categories]
    )
    await message.answer("<b>Select Category</b>", parse_mode="HTML", reply_markup=keyboard)
    await state.set_state(ListingStates.awaiting_category)


@router.callback_query(F.data.startswith("cat_"), StateFilter(ListingStates.awaiting_category))
async def handle_listing_category(callback: CallbackQuery, state: FSMContext):
    await safe_answer_callback(callback)
    category_str = callback.data.replace("cat_", "").upper()
    try:
        category = Category[category_str]
    except KeyError:
        await safe_answer_callback(callback, text="Invalid category", show_alert=True)
        return

    await state.update_data(category=category)
    await safe_edit_text(
        callback,
        "<b>Base Price (NGN)</b>\n\nEnter base price. Buyer pays 5% platform fee.",
        parse_mode="HTML",
    )
    await state.set_state(ListingStates.awaiting_base_price)


@router.message(ListingStates.awaiting_base_price)
async def handle_listing_price(message: Message, state: FSMContext):
    try:
        price = Decimal(message.text.strip())
        if price <= 0:
            raise ValueError()
    except Exception:
        await message.reply("Please enter a valid price (e.g., 5000).")
        return

    buyer_price = (price * Decimal("1.05")).quantize(Decimal("0.01"), rounding=ROUND_CEILING)
    await state.update_data(base_price=price, buyer_price=buyer_price)
    data = await state.get_data()

    text = (
        "<b>Listing Summary</b>\n\n"
        f"Title: {data.get('title')}\n"
        f"Description: {data.get('description')}\n"
        f"Category: {data.get('category').value}\n"
        f"Base Price: NGN {data.get('base_price'):,.2f}\n"
        f"Buyer Price: NGN {data.get('buyer_price'):,.2f}\n\n"
        "Create this listing?"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Create", callback_data="listing_confirm"),
                InlineKeyboardButton(text="Cancel", callback_data="back_to_menu"),
            ]
        ]
    )
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)
    await state.set_state(ListingStates.confirming_listing)


@router.callback_query(F.data == "listing_confirm", StateFilter(ListingStates.confirming_listing))
async def confirm_listing_creation(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    await safe_answer_callback(callback)
    data = await state.get_data()
    try:
        listing = Listing(
            seller_id=data.get("seller_id"),
            title=data.get("title"),
            description=data.get("description"),
            category=data.get("category"),
            base_price=data.get("base_price"),
            buyer_price=data.get("buyer_price"),
            available=True,
        )
        session.add(listing)
        await session.commit()
        await safe_edit_text(
            callback,
            (
                "<b>Listing Created</b>\n\n"
                "Your product is now live.\n"
                f"Listing ID: {listing.id}"
            ),
            parse_mode="HTML",
            reply_markup=get_seller_actions(),
        )
    except Exception as e:
        await session.rollback()
        await safe_edit_text(callback, f"Error: {e}", reply_markup=get_seller_actions())

    await state.clear()
