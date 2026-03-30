"""
Buyer catalog and product browsing handler.
"""

import time

from aiogram import F, Router
from aiogram.exceptions import TelegramRetryAfter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from bot.helpers.telegram import safe_answer_callback, safe_edit_text
from bot.keyboards.main_menu import get_catalog_categories
from db.models import Category, Listing, SellerProfile

router = Router()

PAGE_SIZE = 10
CACHE_TTL_SECONDS = 20
_CATEGORY_CACHE: dict[str, tuple[float, list[Listing]]] = {}


def format_category_label(category: Category) -> str:
    if category == Category.ELECTRONICS:
        return "Laptop"
    if category == Category.SKINCARE:
        return "Skin Care"
    return category.value.title()


async def _fetch_category_listings(session: AsyncSession, category: Category) -> list[Listing]:
    cache_key = category.name
    now = time.time()
    cached = _CATEGORY_CACHE.get(cache_key)
    if cached and now - cached[0] <= CACHE_TTL_SECONDS:
        return cached[1]

    result = await session.execute(
        select(Listing)
        .options(joinedload(Listing.seller).joinedload(SellerProfile.user))
        .where(Listing.category == category)
        .where(Listing.available == True)
        .order_by(Listing.created_at.desc())
        .limit(200)
    )
    listings = result.scalars().all()
    _CATEGORY_CACHE[cache_key] = (now, listings)
    return listings


def _build_category_page_keyboard(category: Category, total_items: int, page: int) -> InlineKeyboardMarkup:
    total_pages = (total_items - 1) // PAGE_SIZE + 1 if total_items else 1

    rows: list[list[InlineKeyboardButton]] = []
    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(
            InlineKeyboardButton(
                text="Prev 10",
                callback_data=f"browse_page_{category.name}_{page - 1}",
            )
        )
    if page + 1 < total_pages:
        nav_row.append(
            InlineKeyboardButton(
                text="Next 10",
                callback_data=f"browse_page_{category.name}_{page + 1}",
            )
        )
    if nav_row:
        rows.append(nav_row)
    rows.append([InlineKeyboardButton(text="Back to Categories", callback_data="browse_catalog")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_listing_card_keyboard(listing: Listing) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Buy Now", callback_data=f"buy_listing_{listing.id}")],
            [InlineKeyboardButton(text="Seller Profile", callback_data=f"seller_profile_{listing.seller_id}")],
        ]
    )


async def _send_listing_card(callback: CallbackQuery, listing: Listing, card_text: str) -> None:
    card_keyboard = _build_listing_card_keyboard(listing)
    for _ in range(2):
        try:
            if listing.image_url:
                await callback.message.answer_photo(
                    photo=listing.image_url,
                    caption=card_text,
                    parse_mode="HTML",
                    reply_markup=card_keyboard,
                )
            else:
                await callback.message.answer(
                    card_text,
                    parse_mode="HTML",
                    reply_markup=card_keyboard,
                )
            return
        except TelegramRetryAfter as exc:
            await safe_answer_callback(
                callback,
                text="Too many requests right now, retrying...",
                show_alert=False,
            )
            await time_async_sleep(exc.retry_after)


async def time_async_sleep(seconds: float) -> None:
    # Isolated helper to keep send retries readable.
    import asyncio

    await asyncio.sleep(seconds)


async def _show_category_page(
    callback: CallbackQuery,
    category: Category,
    listings: list[Listing],
    page: int,
) -> None:
    if not listings:
        await safe_edit_text(
            callback,
            f"No products available in {format_category_label(category)}.\n\nTry another category.",
            reply_markup=get_catalog_categories(),
        )
        return

    total_pages = (len(listings) - 1) // PAGE_SIZE + 1
    page = max(0, min(page, total_pages - 1))
    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, len(listings))
    page_items = listings[start:end]

    control_text = (
        f"<b>{format_category_label(category)} Listings</b>\n"
        f"Page {page + 1}/{total_pages} ({len(listings)} total)\n\n"
        "Each listing is shown below with its own image."
    )
    control_keyboard = _build_category_page_keyboard(category, len(listings), page)
    # If callback came from a photo message, editing text will mutate caption on that image.
    # Create a fresh text control message instead, so stale images are not reused as headers.
    if callback.message and callback.message.photo:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer(
            control_text,
            parse_mode="HTML",
            reply_markup=control_keyboard,
        )
    else:
        await safe_edit_text(callback, control_text, parse_mode="HTML", reply_markup=control_keyboard)

    for idx, listing in enumerate(page_items, start=start + 1):
        seller_name = listing.seller.user.first_name if listing.seller and listing.seller.user else "Unknown"
        card_text = (
            f"<b>{idx}. {listing.title}</b>\n\n"
            f"{listing.description}\n\n"
            f"Category: {format_category_label(category)}\n"
            f"Price: NGN {listing.buyer_price:,.2f}\n"
            f"Seller: {seller_name}"
        )
        await _send_listing_card(callback, listing, card_text)


@router.callback_query(F.data == "browse_catalog")
async def browse_catalog(callback: CallbackQuery):
    await safe_answer_callback(callback)
    text = "Browse Catalog\n\nSelect a category to view available products:"
    await safe_edit_text(callback, text, reply_markup=get_catalog_categories())


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
    await _show_category_page(callback, category, listings, page=0)


@router.callback_query(F.data.startswith("browse_page_"))
async def browse_category_page(callback: CallbackQuery, session: AsyncSession):
    await safe_answer_callback(callback)
    try:
        _, _, category_name, page_str = callback.data.split("_", maxsplit=3)
        category = Category[category_name]
        page = int(page_str)
    except Exception:
        await safe_edit_text(
            callback,
            "Unable to load that page. Please browse the category again.",
            reply_markup=get_catalog_categories(),
        )
        return

    listings = await _fetch_category_listings(session, category)
    await _show_category_page(callback, category, listings, page=page)


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
        await safe_edit_text(callback, "Seller profile not found.", reply_markup=get_catalog_categories())
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
        inline_keyboard=[[InlineKeyboardButton(text="Back to Categories", callback_data="browse_catalog")]]
    )
    await safe_edit_text(callback, text, parse_mode="HTML", reply_markup=keyboard)
