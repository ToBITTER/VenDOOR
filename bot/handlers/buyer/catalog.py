"""
Buyer catalog and product browsing handler.
"""

import time

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from bot.helpers.brand_assets import get_category_hero, get_empty_state, get_welcome_banner
from bot.helpers.telegram import (
    safe_answer_callback,
    safe_edit_text,
    safe_render_text_screen,
    safe_replace_with_screen,
)
from bot.keyboards.main_menu import get_catalog_categories
from db.models import AccessorySubcategory, Category, Listing, SellerProfile

router = Router()

PAGE_SIZE = 10
CACHE_TTL_SECONDS = 20
_CATEGORY_CACHE: dict[str, tuple[float, list[Listing]]] = {}


class CatalogSearchStates(StatesGroup):
    awaiting_query = State()


def _callback_int_suffix(callback_data: str | None, prefix: str) -> int | None:
    payload = (callback_data or "").strip()
    if not payload.startswith(prefix):
        return None
    value = payload.replace(prefix, "", 1).strip()
    if not value.isdigit():
        return None
    return int(value)


def _short(text: str, length: int = 28) -> str:
    clean = (text or "").strip()
    if len(clean) <= length:
        return clean
    return clean[: length - 1].rstrip() + "…"


def format_category_label(category: Category, accessory_subcategory: AccessorySubcategory | None = None) -> str:
    if category == Category.JEWELRY:
        if accessory_subcategory:
            return f"Accessories / {accessory_subcategory.value.title()}"
        return "Accessories"
    if category == Category.ELECTRONICS:
        return "Laptop"
    if category == Category.WIGS:
        return "Wigs"
    if category == Category.OTHERGADGETS:
        return "Other Gadgets"
    if category == Category.SKINCARE:
        return "Skincare & Perfumes"
    return category.value.title()


async def _fetch_category_listings(
    session: AsyncSession,
    category: Category,
    accessory_subcategory: AccessorySubcategory | None = None,
) -> list[Listing]:
    subcat_token = accessory_subcategory.name if accessory_subcategory else "ALL"
    cache_key = f"{category.name}:{subcat_token}"
    now = time.time()
    cached = _CATEGORY_CACHE.get(cache_key)
    if cached and now - cached[0] <= CACHE_TTL_SECONDS:
        return cached[1]

    query = (
        select(Listing)
        .join(SellerProfile, SellerProfile.id == Listing.seller_id)
        .options(joinedload(Listing.seller).joinedload(SellerProfile.user))
        .where(Listing.category == category)
        .where(Listing.available == True)
        .order_by(
            desc(SellerProfile.is_featured),
            Listing.created_at.desc(),
        )
        .limit(200)
    )
    if accessory_subcategory:
        query = query.where(Listing.accessory_subcategory == accessory_subcategory)

    result = await session.execute(query)
    listings = result.scalars().all()
    _CATEGORY_CACHE[cache_key] = (now, listings)
    return listings


def _build_category_page_keyboard(
    category: Category,
    total_items: int,
    page: int,
    subcat_token: str = "ALL",
) -> InlineKeyboardMarkup:
    total_pages = (total_items - 1) // PAGE_SIZE + 1 if total_items else 1

    rows: list[list[InlineKeyboardButton]] = []
    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(
            InlineKeyboardButton(
                text="Prev 10",
                callback_data=f"browse_page_{category.name}_{subcat_token}_{page - 1}",
            )
        )
    if page + 1 < total_pages:
        nav_row.append(
            InlineKeyboardButton(
                text="Next 10",
                callback_data=f"browse_page_{category.name}_{subcat_token}_{page + 1}",
            )
        )
    if nav_row:
        rows.append(nav_row)
    rows.append(
        [
            InlineKeyboardButton(text="Search Listings", callback_data="catalog_search_start"),
            InlineKeyboardButton(text="Checkout Cart", callback_data="cart_checkout"),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(text="Back to Categories", callback_data="browse_catalog"),
            InlineKeyboardButton(text="Back to Main Menu", callback_data="back_to_menu"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_listing_card_keyboard(listing: Listing) -> InlineKeyboardMarkup:
    if listing.quantity <= 0 or not listing.available:
        return InlineKeyboardMarkup(
            inline_keyboard=[
            [InlineKeyboardButton(text="Out of Stock", callback_data="stock_unavailable")],
            [InlineKeyboardButton(text="Checkout Cart", callback_data="cart_checkout")],
            [InlineKeyboardButton(text="Seller Profile", callback_data=f"seller_profile_{listing.seller_id}")],
            ]
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Add to Cart", callback_data=f"add_to_cart_{listing.id}")],
            [InlineKeyboardButton(text="Checkout Cart", callback_data="cart_checkout")],
            [InlineKeyboardButton(text="Seller Profile", callback_data=f"seller_profile_{listing.seller_id}")],
        ]
    )


def _build_category_bottom_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Search Listings", callback_data="catalog_search_start"),
                InlineKeyboardButton(text="Checkout Cart", callback_data="cart_checkout"),
            ],
            [
                InlineKeyboardButton(text="Back to Categories", callback_data="browse_catalog"),
                InlineKeyboardButton(text="Back to Main Menu", callback_data="back_to_menu"),
            ],
        ]
    )


async def _show_category_page(
    callback: CallbackQuery,
    category: Category,
    listings: list[Listing],
    page: int,
    accessory_subcategory: AccessorySubcategory | None = None,
) -> None:
    if not listings:
        empty_image = get_empty_state("no_listings")
        empty_text = (
            f"No products available in {format_category_label(category, accessory_subcategory)}.\n\n"
            "Try another category."
        )
        await safe_replace_with_screen(
            callback,
            empty_text,
            photo=empty_image,
            reply_markup=get_catalog_categories(),
        )
        return

    total_pages = (len(listings) - 1) // PAGE_SIZE + 1
    page = max(0, min(page, total_pages - 1))
    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, len(listings))
    page_items = listings[start:end]

    control_text = (
        f"<b>{format_category_label(category, accessory_subcategory)} Listings</b>\n"
        f"Page {page + 1}/{total_pages} ({len(listings)} total)\n\n"
        "Showing up to 10 listings on this page."
    )
    subcat_token = accessory_subcategory.name if accessory_subcategory else "ALL"
    control_keyboard = _build_category_page_keyboard(
        category,
        len(listings),
        page,
        subcat_token=subcat_token,
    )
    hero = get_category_hero(
        category.name,
        accessory_subcategory.name if accessory_subcategory else None,
    )
    await safe_replace_with_screen(
        callback,
        control_text,
        photo=hero,
        parse_mode="HTML",
        reply_markup=control_keyboard,
    )

    for idx, listing in enumerate(page_items, start=start + 1):
        seller_name = listing.seller.user.first_name if listing.seller and listing.seller.user else "Unknown"
        card_text = (
            f"<b>{idx}. {listing.title}</b>\n\n"
            f"{listing.description}\n\n"
            f"Category: {format_category_label(category, listing.accessory_subcategory)}\n"
            f"Price: NGN {listing.buyer_price:,.2f}\n"
            f"Stock: {'Out of Stock' if listing.quantity <= 0 or not listing.available else f'{listing.quantity} left'}\n"
            f"Seller: {seller_name}"
        )
        keyboard = _build_listing_card_keyboard(listing)
        if listing.image_url:
            await callback.message.answer_photo(
                photo=listing.image_url,
                caption=card_text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        else:
            await callback.message.answer(
                card_text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )

    await callback.message.answer(
        "End of this page.",
        reply_markup=_build_category_bottom_keyboard(),
    )


@router.callback_query(F.data == "browse_catalog")
async def browse_catalog(callback: CallbackQuery):
    await safe_answer_callback(callback)
    text = "<b>Browse Marketplace</b>\n\nSelect a category to see available products:"
    welcome_banner = get_welcome_banner()
    if welcome_banner:
        await safe_replace_with_screen(
            callback,
            text,
            photo=welcome_banner,
            parse_mode="HTML",
            reply_markup=get_catalog_categories(),
        )
        return
    await safe_render_text_screen(callback, text, parse_mode="HTML", reply_markup=get_catalog_categories())


@router.callback_query(F.data == "catalog_search_start")
async def catalog_search_start(callback: CallbackQuery, state: FSMContext):
    await safe_answer_callback(callback)
    await state.clear()
    await state.set_state(CatalogSearchStates.awaiting_query)
    await safe_replace_with_screen(
        callback,
        "<b>Search Listings</b>\n\nSend a keyword (product title or description).",
        parse_mode="HTML",
        reply_markup=_build_category_bottom_keyboard(),
    )


@router.message(CatalogSearchStates.awaiting_query)
async def catalog_search_query(message: Message, state: FSMContext, session: AsyncSession):
    query_text = (message.text or "").strip()
    if len(query_text) < 2:
        await message.answer("Please enter at least 2 characters.")
        return

    result = await session.execute(
        select(Listing)
        .options(joinedload(Listing.seller).joinedload(SellerProfile.user))
        .where(Listing.available == True)
        .where(
            or_(
                Listing.title.ilike(f"%{query_text}%"),
                Listing.description.ilike(f"%{query_text}%"),
            )
        )
        .order_by(desc(Listing.created_at))
        .limit(15)
    )
    listings = result.scalars().all()
    if not listings:
        await message.answer(
            "No matching listings found. Try another keyword.",
            reply_markup=_build_category_bottom_keyboard(),
        )
        return

    rows: list[list[InlineKeyboardButton]] = []
    for idx, listing in enumerate(listings, start=1):
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{idx}. {_short(listing.title)}",
                    callback_data=f"search_view_{listing.id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="Search Again", callback_data="catalog_search_start")])
    rows.append([InlineKeyboardButton(text="Checkout Cart", callback_data="cart_checkout")])
    rows.append(
        [
            InlineKeyboardButton(text="Back to Categories", callback_data="browse_catalog"),
            InlineKeyboardButton(text="Back to Main Menu", callback_data="back_to_menu"),
        ]
    )

    await message.answer(
        f"<b>Search Results</b>\nKeyword: <code>{query_text}</code>\nFound: {len(listings)}\n\nTap an item to view details.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await state.clear()


async def _render_listing_detail(
    callback: CallbackQuery,
    session: AsyncSession,
    listing_id: int,
    back_callback_data: str,
) -> None:
    result = await session.execute(
        select(Listing)
        .options(joinedload(Listing.seller).joinedload(SellerProfile.user))
        .where(Listing.id == listing_id)
    )
    listing = result.scalars().first()
    if not listing:
        await safe_edit_text(callback, "Listing not found.", reply_markup=get_catalog_categories())
        return

    seller_name = listing.seller.user.first_name if listing.seller and listing.seller.user else "Unknown"
    card_text = (
        f"<b>{listing.title}</b>\n\n"
        f"{listing.description}\n\n"
        f"Category: {format_category_label(listing.category, listing.accessory_subcategory)}\n"
        f"Price: NGN {listing.buyer_price:,.2f}\n"
        f"Stock: {'Out of Stock' if listing.quantity <= 0 or not listing.available else f'{listing.quantity} left'}\n"
        f"Seller: {seller_name}"
    )
    base_keyboard = _build_listing_card_keyboard(listing).inline_keyboard
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=base_keyboard
        + [
            [InlineKeyboardButton(text="Back to Results", callback_data=back_callback_data)],
            [InlineKeyboardButton(text="Back to Categories", callback_data="browse_catalog")],
        ]
    )
    await safe_replace_with_screen(
        callback,
        card_text,
        photo=listing.image_url or None,
        parse_mode="HTML",
        reply_markup=keyboard,
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
    if category == Category.JEWELRY:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Bags", callback_data="browse_acc_BAGS_0")],
                [InlineKeyboardButton(text="Jewelry", callback_data="browse_acc_JEWELRY_0")],
                [InlineKeyboardButton(text="Watches", callback_data="browse_acc_WATCHES_0")],
                [InlineKeyboardButton(text="All Accessories", callback_data="browse_acc_ALL_0")],
                [InlineKeyboardButton(text="Back to Categories", callback_data="browse_catalog")],
            ]
        )
        await safe_edit_text(
            callback,
            "<b>Accessories</b>\n\nChoose a subcategory:",
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        return

    listings = await _fetch_category_listings(session, category)
    await _show_category_page(callback, category, listings, page=0, accessory_subcategory=None)


@router.callback_query(F.data.startswith("browse_page_"))
async def browse_category_page(callback: CallbackQuery, session: AsyncSession):
    await safe_answer_callback(callback)
    try:
        _, _, category_name, subcat_token, page_str = callback.data.split("_", maxsplit=4)
        category = Category[category_name]
        accessory_subcategory = None if subcat_token == "ALL" else AccessorySubcategory[subcat_token]
        page = int(page_str)
    except Exception:
        await safe_edit_text(
            callback,
            "Unable to load that page. Please browse the category again.",
            reply_markup=get_catalog_categories(),
        )
        return

    listings = await _fetch_category_listings(session, category, accessory_subcategory)
    await _show_category_page(callback, category, listings, page=page, accessory_subcategory=accessory_subcategory)


@router.callback_query(F.data.startswith("catalog_view_"))
async def view_listing_from_category(callback: CallbackQuery, session: AsyncSession):
    await safe_answer_callback(callback)
    try:
        _, _, listing_id_str, category_name, subcat_token, page_str = callback.data.split("_", maxsplit=5)
        listing_id = int(listing_id_str)
        page = int(page_str)
        # Validate category token to guard malformed callbacks.
        _ = Category[category_name]
        _ = subcat_token
    except Exception:
        await safe_edit_text(
            callback,
            "Unable to open listing details. Please reopen the category.",
            reply_markup=get_catalog_categories(),
        )
        return
    back_cb = f"browse_page_{category_name}_{subcat_token}_{page}"
    await _render_listing_detail(callback, session, listing_id, back_cb)


@router.callback_query(F.data.startswith("search_view_"))
async def view_listing_from_search(callback: CallbackQuery, session: AsyncSession):
    await safe_answer_callback(callback)
    listing_id = _callback_int_suffix(callback.data, "search_view_")
    if listing_id is None:
        await safe_edit_text(
            callback,
            "Unable to open listing details. Please search again.",
            reply_markup=_build_category_bottom_keyboard(),
        )
        return
    await _render_listing_detail(callback, session, listing_id, "catalog_search_start")


@router.callback_query(F.data.startswith("browse_acc_"))
async def browse_accessories_subcategory(callback: CallbackQuery, session: AsyncSession):
    await safe_answer_callback(callback)
    try:
        _, _, subcat_token, page_str = callback.data.split("_", maxsplit=3)
        page = int(page_str)
        accessory_subcategory = None if subcat_token == "ALL" else AccessorySubcategory[subcat_token]
    except Exception:
        await safe_edit_text(
            callback,
            "Unable to load accessories. Please try again.",
            reply_markup=get_catalog_categories(),
        )
        return

    listings = await _fetch_category_listings(session, Category.JEWELRY, accessory_subcategory)
    await _show_category_page(
        callback,
        Category.JEWELRY,
        listings,
        page=page,
        accessory_subcategory=accessory_subcategory,
    )


@router.callback_query(F.data.startswith("seller_profile_"))
async def view_seller_profile(callback: CallbackQuery, session: AsyncSession):
    seller_id = _callback_int_suffix(callback.data, "seller_profile_")
    if seller_id is None:
        await safe_answer_callback(callback, text="Invalid seller profile.", show_alert=True)
        return
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
    active_listings = len([listing for listing in (seller.listings or []) if listing.available])

    text = (
        "<b>Seller Profile</b>\n\n"
        f"<b>Name:</b> {seller_name}\n"
        "<b>Username:</b> Hidden until payment is confirmed\n"
        f"<b>Verification:</b> {'Verified' if seller.verified else 'Pending'}\n"
        f"<b>Featured Vendor:</b> {'Yes' if seller.is_featured else 'No'}\n"
        f"<b>Active Listings:</b> {active_listings}\n"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Back to Categories", callback_data="browse_catalog")],
            [InlineKeyboardButton(text="Back to Main Menu", callback_data="back_to_menu")],
        ]
    )
    await safe_edit_text(callback, text, parse_mode="HTML", reply_markup=keyboard)


@router.callback_query(F.data == "stock_unavailable")
async def stock_unavailable(callback: CallbackQuery):
    await safe_answer_callback(callback, text="This item is out of stock.", show_alert=True)
