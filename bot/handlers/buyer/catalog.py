"""
Buyer catalog and product browsing handler.
"""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from bot.helpers.telegram import safe_answer_callback, safe_edit_text
from bot.keyboards.main_menu import get_catalog_categories
from db.models import Category, Listing, SellerProfile

router = Router()


def format_category_label(category: Category) -> str:
    if category == Category.ELECTRONICS:
        return "Laptop"
    if category == Category.SKINCARE:
        return "Skin Care"
    return category.value.title()


async def _fetch_category_listings(session: AsyncSession, category: Category) -> list[Listing]:
    result = await session.execute(
        select(Listing)
        .options(joinedload(Listing.seller).joinedload(SellerProfile.user))
        .where(Listing.category == category)
        .where(Listing.available == True)
        .order_by(Listing.created_at.desc())
        .limit(30)
    )
    return result.scalars().all()


def _build_listing_keyboard(category: Category, listing: Listing, index: int, total: int) -> InlineKeyboardMarkup:
    nav_row: list[InlineKeyboardButton] = []
    if index > 0:
        nav_row.append(
            InlineKeyboardButton(
                text="Prev",
                callback_data=f"browse_item_{category.name}_{index - 1}",
            )
        )
    if index < total - 1:
        nav_row.append(
            InlineKeyboardButton(
                text="Next",
                callback_data=f"browse_item_{category.name}_{index + 1}",
            )
        )

    rows: list[list[InlineKeyboardButton]] = []
    if nav_row:
        rows.append(nav_row)
    rows.extend(
        [
            [InlineKeyboardButton(text="Buy Now", callback_data=f"buy_listing_{listing.id}")],
            [InlineKeyboardButton(text="Seller Profile", callback_data=f"seller_profile_{listing.seller_id}")],
            [InlineKeyboardButton(text="Back to Categories", callback_data="browse_catalog")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_listing(
    callback: CallbackQuery,
    category: Category,
    listings: list[Listing],
    index: int,
) -> None:
    listing = listings[index]
    total = len(listings)
    text = (
        f"<b>{listing.title}</b>\n"
        f"Item {index + 1}/{total}\n\n"
        f"{listing.description}\n\n"
        f"Category: {format_category_label(category)}\n"
        f"Price: NGN {listing.buyer_price:,.2f}\n"
        f"Seller: {listing.seller.user.first_name}"
    )

    keyboard = _build_listing_keyboard(category, listing, index, total)

    if listing.image_url:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer_photo(
            photo=listing.image_url,
            caption=text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    else:
        await safe_edit_text(
            callback,
            text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )


@router.callback_query(F.data == "browse_catalog")
async def browse_catalog(callback: CallbackQuery):
    await safe_answer_callback(callback)
    text = "Browse Catalog\n\nSelect a category to view available products:"
    await safe_edit_text(
        callback,
        text,
        reply_markup=get_catalog_categories(),
    )


@router.callback_query(F.data.startswith("browse_cat_"))
async def browse_category(callback: CallbackQuery, session: AsyncSession):
    category_str = callback.data.replace("browse_cat_", "").upper()

    try:
        category = Category[category_str]
    except KeyError:
        await safe_answer_callback(callback, text="Invalid category", show_alert=True)
        return
    await safe_answer_callback(callback)

    listings = await _fetch_category_listings(session, category)

    if not listings:
        await safe_edit_text(
            callback,
            f"No products available in {format_category_label(category)}.\n\nTry another category.",
            reply_markup=get_catalog_categories(),
        )
        return

    await _show_listing(callback, category, listings, index=0)


@router.callback_query(F.data.startswith("browse_item_"))
async def browse_category_item(callback: CallbackQuery, session: AsyncSession):
    await safe_answer_callback(callback)
    try:
        _, _, category_name, index_str = callback.data.split("_", maxsplit=3)
        category = Category[category_name]
        index = int(index_str)
    except Exception:
        await safe_edit_text(
            callback,
            "Unable to load that listing. Please browse the category again.",
            reply_markup=get_catalog_categories(),
        )
        return

    listings = await _fetch_category_listings(session, category)
    if not listings:
        await safe_edit_text(
            callback,
            f"No products available in {format_category_label(category)}.\n\nTry another category.",
            reply_markup=get_catalog_categories(),
        )
        return

    if index < 0 or index >= len(listings):
        index = 0
    await _show_listing(callback, category, listings, index=index)


@router.callback_query(F.data.startswith("buy_listing_"))
async def initiate_buy(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    listing_id = int(callback.data.replace("buy_listing_", ""))

    result = await session.execute(select(Listing).where(Listing.id == listing_id))
    listing = result.scalars().first()

    if not listing:
        await safe_answer_callback(callback, text="Listing not found", show_alert=True)
        return

    await safe_answer_callback(callback)
    await state.update_data(listing_id=listing_id)

    from bot.handlers.buyer import checkout

    await checkout.start_checkout(callback, state, session)


@router.callback_query(F.data.startswith("seller_profile_"))
async def view_seller_profile(callback: CallbackQuery, session: AsyncSession):
    seller_id = int(callback.data.replace("seller_profile_", ""))
    await safe_answer_callback(callback)

    result = await session.execute(
        select(SellerProfile)
        .options(selectinload(SellerProfile.user), selectinload(SellerProfile.listings))
        .where(SellerProfile.id == seller_id)
    )
    seller = result.scalars().first()
    if not seller:
        await safe_edit_text(
            callback,
            "Seller profile not found.",
            reply_markup=get_catalog_categories(),
        )
        return

    user = seller.user
    seller_name = user.first_name if user else "Unknown Seller"
    username = f"@{user.username}" if user and user.username else "N/A"
    active_listings = len([listing for listing in (seller.listings or []) if listing.available])

    text = (
        "<b>Seller Profile</b>\n\n"
        f"<b>Name:</b> {seller_name}\n"
        f"<b>Username:</b> {username}\n"
        f"<b>Verification:</b> {'Verified' if seller.verified else 'Pending'}\n"
        f"<b>Active Listings:</b> {active_listings}\n"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Back to Categories", callback_data="browse_catalog")],
        ]
    )
    await safe_edit_text(callback, text, parse_mode="HTML", reply_markup=keyboard)
