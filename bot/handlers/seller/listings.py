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

from bot.helpers.telegram import safe_answer_callback, safe_replace_with_screen
from bot.keyboards.main_menu import get_main_menu_inline, get_seller_actions
from db.models import AccessorySubcategory, Category, Listing, SellerProfile, User

router = Router()
MINIMUM_LISTING_PRICE = Decimal("500")


def format_category_label(category: Category, accessory_subcategory: AccessorySubcategory | None = None) -> str:
    if category == Category.JEWELRY:
        base = "Accessories"
        if accessory_subcategory:
            return f"{base} / {accessory_subcategory.value.title()}"
        return base
    if category == Category.ELECTRONICS:
        return "Laptop"
    if category == Category.SKINCARE:
        return "Skin Care"
    return category.value.title()


class ListingStates(StatesGroup):
    awaiting_title = State()
    awaiting_description = State()
    awaiting_image = State()
    awaiting_category = State()
    awaiting_accessory_subcategory = State()
    awaiting_quantity = State()
    awaiting_base_price = State()
    confirming_listing = State()


QUANTITY_ENABLED_CATEGORIES = {
    Category.CLOTHES,
    Category.JEWELRY,
    Category.SKINCARE,
    Category.BOOKS,
    Category.SHOES,
}


def category_uses_quantity(category: Category) -> bool:
    return category in QUANTITY_ENABLED_CATEGORIES


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
        await safe_replace_with_screen(
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
        await safe_replace_with_screen(
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
            f"Listing ID: {listing.listing_code}\n"
            f"Price: NGN {listing.buyer_price:,.2f}\n"
            f"Quantity: {listing.quantity}\n"
            f"Status: {status}\n"
            f"Category: {format_category_label(listing.category, listing.accessory_subcategory)}\n\n"
        )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Create New Listing", callback_data="seller_create_listing")],
            [InlineKeyboardButton(text="Back", callback_data="back_to_menu")],
        ]
    )
    await safe_replace_with_screen(callback, text, parse_mode="HTML", reply_markup=keyboard)


@router.callback_query(F.data == "seller_create_listing")
async def start_create_listing(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    await safe_answer_callback(callback)
    user_id_str = str(callback.from_user.id)
    result = await session.execute(
        select(SellerProfile).join(User).where(User.telegram_id == user_id_str)
    )
    seller = result.scalars().first()

    if not seller:
        await safe_replace_with_screen(
            callback,
            "You must be registered as a seller first.",
            reply_markup=get_main_menu_inline(),
        )
        return

    if not seller.verified:
        await safe_replace_with_screen(
            callback,
            "Your seller account is still pending verification.\n\n"
            "You can create listings after verification.",
            reply_markup=get_seller_actions(),
        )
        return

    await state.update_data(seller_id=seller.id)
    await safe_replace_with_screen(
        callback,
        "<b>Create New Listing</b>\n\nWhat is the product title?",
        parse_mode="HTML",
    )
    await state.set_state(ListingStates.awaiting_title)


@router.message(ListingStates.awaiting_title)
async def handle_listing_title(message: Message, state: FSMContext):
    title = (message.text or "").strip()
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
    description = (message.text or "").strip()
    if len(description) < 10 or len(description) > 500:
        await message.reply("Description must be 10-500 characters.")
        return
    await state.update_data(description=description)
    await message.answer(
        "<b>Product Image</b>\n\nSend a clear photo of the item you want to list.",
        parse_mode="HTML",
    )
    await state.set_state(ListingStates.awaiting_image)


@router.message(ListingStates.awaiting_image, F.photo)
async def handle_listing_image(message: Message, state: FSMContext):
    image_file_id = message.photo[-1].file_id
    await state.update_data(image_url=image_file_id)

    categories = [
        ("iPads", "cat_IPADS"),
        ("iPods", "cat_IPODS"),
        ("Accessories", "cat_JEWELRY"),
        ("Clothes", "cat_CLOTHES"),
        ("Laptop", "cat_ELECTRONICS"),
        ("Skin Care", "cat_SKINCARE"),
        ("Books", "cat_BOOKS"),
        ("Shoes", "cat_SHOES"),
        ("Others", "cat_OTHERS"),
    ]
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=cb)] for label, cb in categories]
    )
    await message.answer("<b>Select Category</b>", parse_mode="HTML", reply_markup=keyboard)
    await state.set_state(ListingStates.awaiting_category)


@router.message(ListingStates.awaiting_image)
async def handle_listing_image_invalid(message: Message):
    await message.reply("Please send a product photo to continue.")


@router.callback_query(F.data.startswith("cat_"), StateFilter(ListingStates.awaiting_category))
async def handle_listing_category(callback: CallbackQuery, state: FSMContext):
    await safe_answer_callback(callback)
    category_str = callback.data.replace("cat_", "").upper()
    try:
        category = Category[category_str]
    except KeyError:
        await safe_answer_callback(callback, text="Invalid category", show_alert=True)
        return

    if category == Category.JEWELRY:
        await state.update_data(category=category, accessory_subcategory=None)
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Bags", callback_data="acc_BAGS")],
                [InlineKeyboardButton(text="Jewelry", callback_data="acc_JEWELRY")],
                [InlineKeyboardButton(text="Watches", callback_data="acc_WATCHES")],
            ]
        )
        await safe_replace_with_screen(
            callback,
            "<b>Accessories Type</b>\n\nChoose a subcategory.",
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        await state.set_state(ListingStates.awaiting_accessory_subcategory)
        return

    await state.update_data(category=category, accessory_subcategory=None)
    if category_uses_quantity(category):
        await safe_replace_with_screen(
            callback,
            "<b>Item Quantity</b>\n\nHow many units are available? (1-500)",
            parse_mode="HTML",
        )
        await state.set_state(ListingStates.awaiting_quantity)
        return

    await state.update_data(quantity=1)
    await safe_replace_with_screen(
        callback,
        "<b>Base Price (NGN)</b>\n\nEnter base price. Buyer pays 5% platform fee.",
        parse_mode="HTML",
    )
    await state.set_state(ListingStates.awaiting_base_price)


@router.callback_query(F.data.startswith("acc_"), StateFilter(ListingStates.awaiting_accessory_subcategory))
async def handle_accessory_subcategory(callback: CallbackQuery, state: FSMContext):
    await safe_answer_callback(callback)
    accessory_str = callback.data.replace("acc_", "").upper()
    try:
        accessory_subcategory = AccessorySubcategory[accessory_str]
    except KeyError:
        await safe_answer_callback(callback, text="Invalid accessories type", show_alert=True)
        return

    await state.update_data(accessory_subcategory=accessory_subcategory)
    await safe_replace_with_screen(
        callback,
        "<b>Item Quantity</b>\n\nHow many units are available? (1-500)",
        parse_mode="HTML",
    )
    await state.set_state(ListingStates.awaiting_quantity)


@router.message(ListingStates.awaiting_quantity)
async def handle_listing_quantity(message: Message, state: FSMContext):
    raw_value = (message.text or "").strip()
    if not raw_value.isdigit():
        await message.reply("Please enter a valid quantity (number).")
        return

    quantity = int(raw_value)
    if quantity < 1 or quantity > 500:
        await message.reply("Quantity must be between 1 and 500.")
        return

    await state.update_data(quantity=quantity)
    await message.answer(
        "<b>Base Price (NGN)</b>\n\nEnter base price. Buyer pays 5% platform fee.",
        parse_mode="HTML",
    )
    await state.set_state(ListingStates.awaiting_base_price)


@router.message(ListingStates.awaiting_base_price)
async def handle_listing_price(message: Message, state: FSMContext):
    try:
        price = Decimal((message.text or "").strip())
        if price < MINIMUM_LISTING_PRICE:
            raise ValueError()
    except Exception:
        await message.reply("Listing price must be at least NGN 500.")
        return

    buyer_price = (price * Decimal("1.05")).quantize(Decimal("0.01"), rounding=ROUND_CEILING)
    await state.update_data(base_price=price, buyer_price=buyer_price)
    data = await state.get_data()

    text = (
        "<b>Listing Summary</b>\n\n"
        f"Title: {data.get('title')}\n"
        f"Description: {data.get('description')}\n"
        f"Image: {'Attached' if data.get('image_url') else 'Not attached'}\n"
        f"Category: {format_category_label(data.get('category'), data.get('accessory_subcategory'))}\n"
        f"Quantity: {data.get('quantity', 1)}\n"
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
            image_url=data.get("image_url"),
            category=data.get("category"),
            accessory_subcategory=data.get("accessory_subcategory"),
            base_price=data.get("base_price"),
            buyer_price=data.get("buyer_price"),
            quantity=data.get("quantity", 1),
            available=True,
        )
        session.add(listing)
        await session.commit()
        await safe_replace_with_screen(
            callback,
            (
                "<b>Listing Created</b>\n\n"
                "Your product is now live.\n"
                f"Listing ID: {listing.listing_code}\n"
                f"Quantity: {listing.quantity}"
            ),
            parse_mode="HTML",
            reply_markup=get_seller_actions(),
        )
    except Exception as e:
        await session.rollback()
        await safe_replace_with_screen(callback, f"Error: {e}", reply_markup=get_seller_actions())

    await state.clear()
