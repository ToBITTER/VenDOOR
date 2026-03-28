"""
Buyer catalog and product browsing handler.
"""

from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Listing, Category
from bot.keyboards.main_menu import get_main_menu_inline, get_catalog_categories

router = Router()


@router.callback_query(F.data == "browse_catalog")
async def browse_catalog(callback: CallbackQuery):
    """
    Show category selection for browsing.
    """
    text = (
        "🛍️ <b>Browse Catalog</b>\n\n"
        "Select a category to view available products:"
    )
    
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=get_catalog_categories(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("browse_cat_"))
async def browse_category(callback: CallbackQuery, session: AsyncSession):
    """
    Show products in selected category.
    """
    # Extract category from callback data
    category_str = callback.data.replace("browse_cat_", "").upper()
    
    try:
        category = Category[category_str]
    except KeyError:
        await callback.answer("❌ Invalid category", show_alert=True)
        return
    
    # Fetch listings in this category
    result = await session.execute(
        select(Listing)
        .where(Listing.category == category)
        .where(Listing.available == True)
        .order_by(Listing.created_at.desc())
        .limit(10)
    )
    listings = result.scalars().all()
    
    if not listings:
        await callback.message.edit_text(
            f"📭 No products available in {category.value}.\n\n"
            "Try another category!",
            reply_markup=get_catalog_categories(),
        )
        await callback.answer()
        return
    
    # Show first product
    listing = listings[0]
    text = (
        f"📦 <b>{listing.title}</b>\n\n"
        f"{listing.description}\n\n"
        f"<b>Category:</b> {category.value}\n"
        f"<b>Price:</b> ₦{listing.buyer_price:,.2f}\n"
        f"<b>Seller:</b> {listing.seller.user.first_name}\n"
        f"<b>Rating:</b> ⭐⭐⭐⭐⭐"
    )
    
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Buy Now", callback_data=f"buy_listing_{listing.id}")],
            [InlineKeyboardButton(text="👤 Seller Profile", callback_data=f"seller_profile_{listing.seller_id}")],
            [InlineKeyboardButton(text="◀️ Back to Categories", callback_data="browse_catalog")],
        ]
    )
    
    if listing.image_url:
        # TODO: If image_url is a Telegram file_id, send photo instead
        pass
    
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("buy_listing_"))
async def initiate_buy(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    """
    Initiate purchase flow for a listing.
    """
    listing_id = int(callback.data.replace("buy_listing_", ""))
    
    # Get listing
    result = await session.execute(
        select(Listing).where(Listing.id == listing_id)
    )
    listing = result.scalars().first()
    
    if not listing:
        await callback.answer("❌ Listing not found", show_alert=True)
        return
    
    # Store in state for checkout flow
    await state.update_data(listing_id=listing_id)
    
    # Trigger checkout FSM
    from bot.handlers.buyer import checkout
    await checkout.start_checkout(callback, state, session)
