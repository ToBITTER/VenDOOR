"""
Seller listings handler - create, view, edit listings.
"""

from decimal import Decimal, ROUND_CEILING
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import User, SellerProfile, Listing, Category
from bot.keyboards.main_menu import get_main_menu_inline, get_seller_actions

router = Router()


class ListingStates(StatesGroup):
    """FSM states for listing creation."""
    awaiting_title = State()
    awaiting_description = State()
    awaiting_category = State()
    awaiting_base_price = State()
    confirming_listing = State()


@router.callback_query(F.data == "seller_listings")
async def view_seller_listings(callback: CallbackQuery, session: AsyncSession):
    """
    Show seller's own listings.
    """
    user_id_str = str(callback.from_user.id)
    
    # Get user and seller profile
    result = await session.execute(
        select(User).where(User.telegram_id == user_id_str)
    )
    user = result.scalars().first()
    
    if not user:
        await callback.answer("User not found", show_alert=True)
        return
    
    result = await session.execute(
        select(SellerProfile).where(SellerProfile.user_id == user.id)
    )
    seller = result.scalars().first()
    
    if not seller:
        await callback.message.edit_text(
            "📭 You're not registered as a seller.\n\n"
            "Register now to start selling!",
            reply_markup=get_main_menu_inline(),
        )
        await callback.answer()
        return
    
    # Get seller's listings
    result = await session.execute(
        select(Listing)
        .where(Listing.seller_id == seller.id)
        .order_by(Listing.created_at.desc())
    )
    listings = result.scalars().all()
    
    if not listings:
        await callback.message.edit_text(
            "📭 You haven't created any listings yet.\n\n"
            "Create your first listing now!",
            reply_markup=get_seller_actions(),
        )
        await callback.answer()
        return
    
    # Show listings
    text = "📋 <b>My Listings</b>\n\n"
    for listing in listings[:10]:
        status = "✅ Active" if listing.available else "❌ Inactive"
        text += (
            f"📦 <b>{listing.title}</b>\n"
            f"  Price: ₦{listing.buyer_price:,.2f}\n"
            f"  Status: {status}\n"
            f"  Category: {listing.category.value}\n\n"
        )
    
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Create New Listing", callback_data="seller_create_listing")],
            [InlineKeyboardButton(text="◀️ Back", callback_data="back_to_menu")],
        ]
    )
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "seller_create_listing")
async def start_create_listing(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    """
    Start listing creation FSM.
    """
    user_id_str = str(callback.from_user.id)
    
    # Check if user is a seller
    result = await session.execute(
        select(SellerProfile)
        .join(User)
        .where(User.telegram_id == user_id_str)
    )
    seller = result.scalars().first()
    
    if not seller:
        await callback.message.edit_text(
            "❌ You must be registered as a seller first.",
            reply_markup=get_main_menu_inline(),
        )
        await callback.answer()
        return
    
    if not seller.verified:
        await callback.message.edit_text(
            "⏳ Your seller account is still pending verification.\n\n"
            "You'll be able to create listings once verified.",
            reply_markup=get_seller_actions(),
        )
        await callback.answer()
        return
    
    await state.update_data(seller_id=seller.id)
    
    await callback.message.edit_text(
        "📦 <b>Create New Listing</b>\n\n"
        "What's the product name/title?",
        parse_mode="HTML",
    )
    await state.set_state(ListingStates.awaiting_title)
    await callback.answer()


@router.message(ListingStates.awaiting_title)
async def handle_listing_title(message: Message, state: FSMContext):
    """Collect product title."""
    title = message.text.strip()
    
    if len(title) < 5:
        await message.reply("❌ Title must be at least 5 characters.")
        return
    
    await state.update_data(title=title)
    
    await message.answer(
        "📝 <b>Product Description</b>\n\n"
        "Describe the product. Include condition, features, etc.\n"
        "Max 500 characters.",
        parse_mode="HTML",
    )
    await state.set_state(ListingStates.awaiting_description)


@router.message(ListingStates.awaiting_description)
async def handle_listing_description(message: Message, state: FSMContext):
    """Collect product description."""
    description = message.text.strip()
    
    if len(description) < 10 or len(description) > 500:
        await message.reply("❌ Description must be 10-500 characters.")
        return
    
    await state.update_data(description=description)
    
    # Show category selection
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    categories = [
        ("📱 iPads", "cat_IPADS"),
        ("🎵 iPods", "cat_IPODS"),
        ("💍 Jewelry", "cat_JEWELRY"),
        ("👕 Clothes", "cat_CLOTHES"),
        ("⚡ Electronics", "cat_ELECTRONICS"),
        ("📖 Books", "cat_BOOKS"),
        ("👟 Shoes", "cat_SHOES"),
        ("🎁 Others", "cat_OTHERS"),
    ]
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=callback)]
            for label, callback in categories
        ]
    )
    
    await message.answer(
        "📂 <b>Select Category</b>",
        parse_mode="HTML",
        reply_markup=keyboard,
    )
    await state.set_state(ListingStates.awaiting_category)


@router.callback_query(F.data.startswith("cat_"), StateFilter(ListingStates.awaiting_category))
async def handle_listing_category(callback: CallbackQuery, state: FSMContext):
    """Collect product category."""
    category_str = callback.data.replace("cat_", "").upper()
    
    try:
        category = Category[category_str]
    except KeyError:
        await callback.answer("❌ Invalid category", show_alert=True)
        return
    
    await state.update_data(category=category)
    
    await callback.message.edit_text(
        "💰 <b>Base Price (₦)</b>\n\n"
        "Enter the base price. The buyer will pay 5% extra as platform fee.\n"
        f"Example: 5000 (buyer pays ₦5,250)",
        parse_mode="HTML",
    )
    await state.set_state(ListingStates.awaiting_base_price)
    await callback.answer()


@router.message(ListingStates.awaiting_base_price)
async def handle_listing_price(message: Message, state: FSMContext):
    """Collect base price."""
    try:
        price = Decimal(message.text.strip())
        if price <= 0:
            raise ValueError("Price must be positive")
    except (ValueError, Exception):
        await message.reply("❌ Please enter a valid price (e.g., 5000).")
        return
    
    buyer_price = (price * Decimal("1.05")).quantize(Decimal("0.01"), rounding=ROUND_CEILING)
    await state.update_data(base_price=price, buyer_price=buyer_price)
    
    # Show confirmation
    data = await state.get_data()
    text = (
        f"✅ <b>Listing Summary</b>\n\n"
        f"<b>Title:</b> {data.get('title')}\n"
        f"<b>Description:</b> {data.get('description')}\n"
        f"<b>Category:</b> {data.get('category').value}\n"
        f"<b>Base Price:</b> ₦{data.get('base_price'):,.2f}\n"
        f"<b>Buyer Price:</b> ₦{data.get('buyer_price'):,.2f} (incl. 5% fee)\n\n"
        f"Create this listing?"
    )
    
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Create", callback_data="listing_confirm"),
                InlineKeyboardButton(text="❌ Cancel", callback_data="back_to_menu"),
            ]
        ]
    )
    
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)
    await state.set_state(ListingStates.confirming_listing)


@router.callback_query(F.data == "listing_confirm", StateFilter(ListingStates.confirming_listing))
async def confirm_listing_creation(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    """
    Create listing in database.
    """
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
        
        await callback.message.edit_text(
            f"🎉 <b>Listing Created!</b>\n\n"
            f"✅ Your product is now live.\n"
            f"📦 Listing ID: {listing.id}\n\n"
            f"Buyers can now see and purchase your product!",
            parse_mode="HTML",
            reply_markup=get_seller_actions(),
        )
        
    except Exception as e:
        await session.rollback()
        await callback.message.edit_text(
            f"❌ Error: {str(e)}",
            reply_markup=get_seller_actions(),
        )
    
    await state.clear()
    await callback.answer()
