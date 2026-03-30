"""
Admin handlers for seller verification workflows.
"""

import asyncio
import re

from aiogram import F, Router
from aiogram.exceptions import TelegramRetryAfter
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from bot.helpers.telegram import safe_answer_callback, safe_edit_text
from core.config import get_settings
from db.models import Order, SellerProfile, User

router = Router()
settings = get_settings()


def _is_admin(telegram_user_id: int) -> bool:
    if not settings.admin_telegram_id:
        return False
    return str(telegram_user_id) == str(settings.admin_telegram_id)


def _pending_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Refresh", callback_data="admin_pending_refresh")],
            [InlineKeyboardButton(text="Back to Admin Tools", callback_data="admin_tools_open")],
            [InlineKeyboardButton(text="Back to Menu", callback_data="back_to_menu")],
        ]
    )


def _actions_keyboard(seller_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Approve", callback_data=f"admin_approve_{seller_id}"),
                InlineKeyboardButton(text="Reject", callback_data=f"admin_reject_{seller_id}"),
            ],
            [InlineKeyboardButton(text="Back to Pending", callback_data="admin_pending_refresh")],
        ]
    )


def _admin_tools_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Stats", callback_data="admin_stats")],
            [InlineKeyboardButton(text="Vendors", callback_data="admin_vendors")],
            [InlineKeyboardButton(text="Transactions", callback_data="admin_transactions")],
            [InlineKeyboardButton(text="Listings", callback_data="admin_listings")],
            [InlineKeyboardButton(text="Pending Sellers", callback_data="admin_pending_refresh")],
            [InlineKeyboardButton(text="Vendor Privileges", callback_data="admin_privileges_help")],
            [InlineKeyboardButton(text="Broadcast", callback_data="admin_broadcast_help")],
            [InlineKeyboardButton(text="Delete Listing", callback_data="admin_delete_help_listing")],
            [InlineKeyboardButton(text="Delete Vendor", callback_data="admin_delete_help_vendor")],
            [InlineKeyboardButton(text="Delete User", callback_data="admin_delete_help_user")],
            [InlineKeyboardButton(text="Back to Menu", callback_data="back_to_menu")],
        ]
    )


def _delete_help_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Back to Admin Tools", callback_data="admin_tools_open")],
            [InlineKeyboardButton(text="Back", callback_data="back_to_menu")],
        ]
    )


async def _render_pending_text(session: AsyncSession) -> str:
    result = await session.execute(
        select(SellerProfile)
        .options(joinedload(SellerProfile.user))
        .where(SellerProfile.verified.is_(False))
        .order_by(SellerProfile.created_at.asc())
    )
    pending = result.scalars().all()
    if not pending:
        return "<b>Pending Seller Verifications</b>\n\nNo pending applications."

    text = "<b>Pending Seller Verifications</b>\n\n"
    for seller in pending[:10]:
        user = seller.user
        name = f"{user.first_name} {user.last_name or ''}".strip() if user else "Unknown"
        username = f"@{user.username}" if user and user.username else "N/A"
        text += (
            f"<b>Seller ID:</b> {seller.id}\n"
            f"<b>Name:</b> {name}\n"
            f"<b>Username:</b> {username}\n"
        )
        text += (
            f"<b>Student:</b> {'Yes' if seller.is_student else 'No'}\n"
            f"<b>Email:</b> {seller.student_email or 'N/A'}\n"
            f"<b>Bank:</b> {seller.bank_code} / {seller.account_number}\n"
            f"<b>Account Name:</b> {seller.account_name}\n"
            f"<b>ID Doc:</b> {seller.id_document_url or 'N/A'}\n"
            f"<b>Submitted:</b> {seller.created_at.strftime('%Y-%m-%d %H:%M')}\n"
            f"/review_seller_{seller.id}\n\n"
        )
    return text


async def _render_stats_text(session: AsyncSession) -> str:
    from db.models import Listing, Order, OrderStatus

    total_orders_sq = select(func.count(Order.id)).scalar_subquery()
    completed_orders_sq = (
        select(func.count(Order.id)).where(Order.status == OrderStatus.COMPLETED).scalar_subquery()
    )
    verified_sellers_sq = (
        select(func.count(SellerProfile.id)).where(SellerProfile.verified.is_(True)).scalar_subquery()
    )
    active_listings_sq = (
        select(func.count(Listing.id)).where(Listing.available.is_(True)).scalar_subquery()
    )

    result = await session.execute(
        select(
            total_orders_sq.label("total_orders"),
            completed_orders_sq.label("completed_orders"),
            verified_sellers_sq.label("verified_sellers"),
            active_listings_sq.label("active_listings"),
        )
    )
    stats = result.one()
    return (
        "<b>Admin Stats</b>\n\n"
        f"<b>Total Orders:</b> {stats.total_orders or 0}\n"
        f"<b>Completed Orders:</b> {stats.completed_orders or 0}\n"
        f"<b>Verified Sellers:</b> {stats.verified_sellers or 0}\n"
        f"<b>Active Listings:</b> {stats.active_listings or 0}"
    )


async def _render_vendors_text(session: AsyncSession, limit: int = 10) -> str:
    from db.models import Listing

    listings_count_sq = (
        select(Listing.seller_id.label("seller_id"), func.count(Listing.id).label("listings_count"))
        .group_by(Listing.seller_id)
        .subquery()
    )
    tx_count_sq = (
        select(Order.seller_id.label("seller_id"), func.count(Order.id).label("transactions_count"))
        .group_by(Order.seller_id)
        .subquery()
    )

    result = await session.execute(
        select(
            SellerProfile,
            User,
            func.coalesce(listings_count_sq.c.listings_count, 0).label("listings_count"),
            func.coalesce(tx_count_sq.c.transactions_count, 0).label("transactions_count"),
        )
        .join(User, SellerProfile.user_id == User.id)
        .outerjoin(listings_count_sq, listings_count_sq.c.seller_id == SellerProfile.id)
        .outerjoin(tx_count_sq, tx_count_sq.c.seller_id == SellerProfile.id)
        .order_by(SellerProfile.created_at.desc())
        .limit(limit)
    )
    rows = result.all()
    if not rows:
        return "<b>Vendors</b>\n\nNo vendors found."

    text = "<b>Vendors (latest 10)</b>\n\n"
    for seller, user, listings_count, transactions_count in rows:
        text += (
            f"<b>Seller ID:</b> {seller.id}\n"
            f"<b>Name:</b> {user.first_name} {user.last_name or ''}\n"
            f"<b>Verified:</b> {'Yes' if seller.verified else 'No'}\n"
            f"<b>Featured:</b> {'Yes' if seller.is_featured else 'No'} "
            f"(priority {seller.priority_score})\n"
            f"<b>Listings:</b> {listings_count} | <b>Tx:</b> {transactions_count}\n\n"
        )
    return text


async def _render_transactions_text(session: AsyncSession, limit: int = 10) -> str:
    result = await session.execute(
        select(Order)
        .options(
            joinedload(Order.buyer),
            joinedload(Order.seller).joinedload(SellerProfile.user),
            joinedload(Order.listing),
        )
        .order_by(Order.created_at.desc())
        .limit(limit)
    )
    orders = result.scalars().all()
    if not orders:
        return "<b>Transactions</b>\n\nNo transactions found."

    text = "<b>Transactions (latest 10)</b>\n\n"
    for order in orders:
        seller_name = order.seller.user.first_name if order.seller and order.seller.user else "Unknown"
        buyer_name = order.buyer.first_name if order.buyer else "Unknown"
        title = order.listing.title if order.listing else "Unknown listing"
        text += (
            f"<b>Order ID:</b> {order.id}\n"
            f"<b>Status:</b> {order.status.value}\n"
            f"<b>Amount:</b> {order.amount}\n"
            f"<b>Buyer:</b> {buyer_name} | <b>Seller:</b> {seller_name}\n"
            f"<b>Listing:</b> {title}\n\n"
        )
    return text


async def _render_listings_text(session: AsyncSession, limit: int = 10) -> str:
    from db.models import Listing

    result = await session.execute(
        select(Listing)
        .options(joinedload(Listing.seller).joinedload(SellerProfile.user))
        .order_by(Listing.created_at.desc())
        .limit(limit)
    )
    listings = result.scalars().all()
    if not listings:
        return "<b>Listings</b>\n\nNo listings found."

    text = (
        "<b>Listings (latest 10)</b>\n\n"
        "Delete with:\n"
        "<code>/delete_listing_&lt;listing_id&gt;</code>\n\n"
    )
    for listing in listings:
        seller_name = listing.seller.user.first_name if listing.seller and listing.seller.user else "Unknown"
        text += (
            f"<b>Listing ID:</b> {listing.id}\n"
            f"<b>Title:</b> {listing.title}\n"
            f"<b>Category:</b> {listing.category.value}\n"
            f"<b>Available:</b> {'Yes' if listing.available else 'No'}\n"
            f"<b>Seller:</b> {seller_name}\n\n"
        )
    return text


@router.message(Command("admin_tools"))
async def admin_tools(message: Message):
    if not _is_admin(message.from_user.id):
        await message.reply("You are not authorized to use this command.")
        return

    text = (
        "<b>Admin Tools</b>\n\n"
        "Use the buttons below for admin actions.\n"
        "Stats, Vendors, Transactions, Listings, Pending Sellers, "
        "Privileges, Broadcast and Deletes are all here.\n\n"
        "Delete commands:\n"
        "<code>/delete_listing_123</code>\n"
        "<code>/delete_vendor_45</code>\n"
        "<code>/delete_user_67</code>"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=_admin_tools_keyboard())


@router.message(Command("pending_sellers"))
async def pending_sellers(message: Message, session: AsyncSession):
    if not _is_admin(message.from_user.id):
        await message.reply("You are not authorized to use this command.")
        return

    text = await _render_pending_text(session)
    await message.answer(text, parse_mode="HTML", reply_markup=_pending_keyboard())


@router.message(F.text.regexp(r"^/review_seller_(\d+)$"))
async def review_seller(message: Message, session: AsyncSession):
    if not _is_admin(message.from_user.id):
        await message.reply("You are not authorized to use this command.")
        return

    seller_id = int(message.text.split("_")[-1])
    result = await session.execute(
        select(SellerProfile).options(joinedload(SellerProfile.user)).where(SellerProfile.id == seller_id)
    )
    seller = result.scalars().first()
    if not seller:
        await message.reply("Seller not found.")
        return

    user = seller.user
    name = f"{user.first_name} {user.last_name or ''}".strip() if user else "Unknown"
    username = f"@{user.username}" if user and user.username else "N/A"
    text = (
        "<b>Seller Verification Review</b>\n\n"
        f"<b>Seller ID:</b> {seller.id}\n"
        f"<b>Name:</b> {name}\n"
        f"<b>Username:</b> {username}\n"
        f"<b>Student:</b> {'Yes' if seller.is_student else 'No'}\n"
        f"<b>Featured Vendor:</b> {'Yes' if seller.is_featured else 'No'}\n"
        f"<b>Priority Score:</b> {seller.priority_score}\n"
        f"<b>Student Email:</b> {seller.student_email or 'N/A'}\n"
        f"<b>Hall:</b> {seller.hall or 'N/A'}\n"
        f"<b>Room Number:</b> {seller.room_number or 'N/A'}\n"
        f"<b>Address:</b> {seller.address or 'N/A'}\n"
        f"<b>Bank Code:</b> {seller.bank_code}\n"
        f"<b>Account Number:</b> {seller.account_number}\n"
        f"<b>Account Name:</b> {seller.account_name}\n"
        f"<b>ID Document File:</b> {seller.id_document_url or 'N/A'}"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=_actions_keyboard(seller.id))


@router.message(F.text.regexp(r"^/delete_listing_(\d+)$"))
async def delete_listing_by_command(message: Message, session: AsyncSession):
    if not _is_admin(message.from_user.id):
        await message.reply("You are not authorized to use this command.")
        return

    listing_id = int(message.text.split("_")[-1])
    from db.models import Listing

    listing = await session.get(Listing, listing_id)
    if not listing:
        await message.reply("Listing not found.")
        return

    order_count_result = await session.execute(
        select(func.count(Order.id)).where(Order.listing_id == listing_id)
    )
    order_count = order_count_result.scalar() or 0
    if order_count > 0:
        await message.reply("Cannot delete listing with existing transactions.")
        return

    await session.delete(listing)
    await session.commit()
    await message.reply(f"Listing {listing_id} deleted.")


@router.message(F.text.regexp(r"^/delete_vendor_(\d+)$"))
async def delete_vendor_by_command(message: Message, session: AsyncSession):
    if not _is_admin(message.from_user.id):
        await message.reply("You are not authorized to use this command.")
        return

    seller_id = int(message.text.split("_")[-1])
    seller = await session.get(SellerProfile, seller_id)
    if not seller:
        await message.reply("Vendor not found.")
        return

    order_count_result = await session.execute(
        select(func.count(Order.id)).where(Order.seller_id == seller_id)
    )
    order_count = order_count_result.scalar() or 0
    if order_count > 0:
        await message.reply("Cannot delete vendor with existing transactions.")
        return

    await session.delete(seller)
    await session.commit()
    await message.reply(f"Vendor {seller_id} deleted.")


@router.message(F.text.regexp(r"^/delete_user_(\d+)$"))
async def delete_user_by_command(message: Message, session: AsyncSession):
    if not _is_admin(message.from_user.id):
        await message.reply("You are not authorized to use this command.")
        return

    user_id = int(message.text.split("_")[-1])
    user = await session.get(User, user_id)
    if not user:
        await message.reply("User not found.")
        return

    buyer_order_count_result = await session.execute(
        select(func.count(Order.id)).where(Order.buyer_id == user_id)
    )
    buyer_order_count = buyer_order_count_result.scalar() or 0
    if buyer_order_count > 0:
        await message.reply("Cannot delete user with buyer transactions.")
        return

    seller_result = await session.execute(select(SellerProfile.id).where(SellerProfile.user_id == user_id))
    seller_id = seller_result.scalar_one_or_none()
    if seller_id is not None:
        seller_order_count_result = await session.execute(
            select(func.count(Order.id)).where(Order.seller_id == seller_id)
        )
        seller_order_count = seller_order_count_result.scalar() or 0
        if seller_order_count > 0:
            await message.reply("Cannot delete user with seller transactions.")
            return

    await session.delete(user)
    await session.commit()
    await message.reply(f"User {user_id} deleted.")


@router.message(F.text.regexp(r"^/set_vendor_privilege_(\d+)_(0|1)_(\d{1,3})$"))
async def set_vendor_privilege(message: Message, session: AsyncSession):
    if not _is_admin(message.from_user.id):
        await message.reply("You are not authorized to use this command.")
        return

    match = re.match(r"^/set_vendor_privilege_(\d+)_(0|1)_(\d{1,3})$", message.text or "")
    if not match:
        await message.reply("Invalid command format.")
        return

    seller_id_text, featured_text, priority_text = match.groups()
    seller_id = int(seller_id_text)
    is_featured = featured_text == "1"
    priority_score = int(priority_text)

    if priority_score > 100:
        await message.reply("Priority must be between 0 and 100.")
        return

    seller = await session.get(SellerProfile, seller_id)
    if not seller:
        await message.reply("Vendor not found.")
        return

    seller.is_featured = is_featured
    seller.priority_score = priority_score
    await session.commit()
    await message.reply(
        f"Vendor {seller_id} updated.\nFeatured: {'Yes' if is_featured else 'No'}\nPriority: {priority_score}"
    )


@router.message(F.text.regexp(r"^/broadcast_(all|buyers|sellers|verified_sellers)\s+([\s\S]+)$"))
async def broadcast_from_admin_chat(message: Message, session: AsyncSession):
    if not _is_admin(message.from_user.id):
        await message.reply("You are not authorized to use this command.")
        return

    first_space = message.text.find(" ")
    header = message.text[:first_space]
    body = message.text[first_space + 1 :].strip()
    audience = header.replace("/broadcast_", "")
    if not body:
        await message.reply("Message body cannot be empty.")
        return

    if audience == "all":
        recipients_query = select(User.telegram_id).where(User.telegram_id.is_not(None)).distinct()
    elif audience == "buyers":
        recipients_query = (
            select(User.telegram_id)
            .join(Order, Order.buyer_id == User.id)
            .where(User.telegram_id.is_not(None))
            .distinct()
        )
    elif audience == "sellers":
        recipients_query = (
            select(User.telegram_id)
            .join(SellerProfile, SellerProfile.user_id == User.id)
            .where(User.telegram_id.is_not(None))
            .distinct()
        )
    else:
        recipients_query = (
            select(User.telegram_id)
            .join(SellerProfile, SellerProfile.user_id == User.id)
            .where(SellerProfile.verified.is_(True))
            .where(User.telegram_id.is_not(None))
            .distinct()
        )

    recipient_result = await session.execute(recipients_query)
    recipient_ids = [row[0] for row in recipient_result.all() if row[0]]
    sent = 0
    failed = 0

    for telegram_id in recipient_ids:
        try:
            chat_id = int(telegram_id)
            await message.bot.send_message(chat_id=chat_id, text=body)
            sent += 1
        except TelegramRetryAfter as exc:
            try:
                await asyncio.sleep(exc.retry_after)
                await message.bot.send_message(chat_id=chat_id, text=body)
                sent += 1
            except Exception:
                failed += 1
        except Exception:
            failed += 1

    await message.reply(
        f"Broadcast complete.\nAudience: {audience}\nRecipients: {len(recipient_ids)}\nSent: {sent}\nFailed: {failed}"
    )


@router.callback_query(F.data == "admin_tools_open")
async def open_admin_tools(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)
    text = (
        "<b>Admin Tools</b>\n\n"
        "Available buttons:\n"
        "- Stats\n"
        "- Vendors\n"
        "- Transactions\n"
        "- Listings\n"
        "- Pending Sellers\n"
        "- Vendor Privileges\n"
        "- Broadcast\n\n"
        "Delete commands:\n"
        "<code>/delete_listing_123</code>\n"
        "<code>/delete_vendor_45</code>\n"
        "<code>/delete_user_67</code>\n\n"
        "Use IDs from admin lists before deleting."
    )
    await safe_edit_text(callback, text, parse_mode="HTML", reply_markup=_admin_tools_keyboard())


@router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery, session: AsyncSession):
    if not _is_admin(callback.from_user.id):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)
    text = await _render_stats_text(session)
    await safe_edit_text(callback, text, parse_mode="HTML", reply_markup=_admin_tools_keyboard())


@router.callback_query(F.data == "admin_vendors")
async def admin_vendors(callback: CallbackQuery, session: AsyncSession):
    if not _is_admin(callback.from_user.id):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)
    text = await _render_vendors_text(session)
    await safe_edit_text(callback, text, parse_mode="HTML", reply_markup=_admin_tools_keyboard())


@router.callback_query(F.data == "admin_transactions")
async def admin_transactions(callback: CallbackQuery, session: AsyncSession):
    if not _is_admin(callback.from_user.id):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)
    text = await _render_transactions_text(session)
    await safe_edit_text(callback, text, parse_mode="HTML", reply_markup=_admin_tools_keyboard())


@router.callback_query(F.data == "admin_listings")
async def admin_listings(callback: CallbackQuery, session: AsyncSession):
    if not _is_admin(callback.from_user.id):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)
    text = await _render_listings_text(session)
    await safe_edit_text(callback, text, parse_mode="HTML", reply_markup=_admin_tools_keyboard())


@router.callback_query(F.data.startswith("admin_delete_help_"))
async def admin_delete_help(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)

    target = callback.data.replace("admin_delete_help_", "")
    if target == "listing":
        text = (
            "<b>Delete Listing</b>\n\n"
            "Send command:\n"
            "<code>/delete_listing_&lt;listing_id&gt;</code>\n\n"
            "Example:\n"
            "<code>/delete_listing_123</code>"
        )
    elif target == "vendor":
        text = (
            "<b>Delete Vendor</b>\n\n"
            "Send command:\n"
            "<code>/delete_vendor_&lt;seller_id&gt;</code>\n\n"
            "Example:\n"
            "<code>/delete_vendor_45</code>"
        )
    else:
        text = (
            "<b>Delete User</b>\n\n"
            "Send command:\n"
            "<code>/delete_user_&lt;user_id&gt;</code>\n\n"
            "Example:\n"
            "<code>/delete_user_67</code>"
        )

    await safe_edit_text(callback, text, parse_mode="HTML", reply_markup=_delete_help_keyboard())


@router.callback_query(F.data == "admin_privileges_help")
async def admin_privileges_help(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)
    text = (
        "<b>Vendor Privileges</b>\n\n"
        "Use command:\n"
        "<code>/set_vendor_privilege_&lt;seller_id&gt;_&lt;featured_0_or_1&gt;_&lt;priority_0_to_100&gt;</code>\n\n"
        "Example:\n"
        "<code>/set_vendor_privilege_45_1_90</code>"
    )
    await safe_edit_text(callback, text, parse_mode="HTML", reply_markup=_delete_help_keyboard())


@router.callback_query(F.data == "admin_broadcast_help")
async def admin_broadcast_help(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)
    text = (
        "<b>Broadcast</b>\n\n"
        "Send one of these commands:\n"
        "<code>/broadcast_all Your message</code>\n"
        "<code>/broadcast_buyers Your message</code>\n"
        "<code>/broadcast_sellers Your message</code>\n"
        "<code>/broadcast_verified_sellers Your message</code>"
    )
    await safe_edit_text(callback, text, parse_mode="HTML", reply_markup=_delete_help_keyboard())


@router.callback_query(F.data == "admin_pending_refresh")
async def refresh_pending(callback: CallbackQuery, session: AsyncSession):
    if not _is_admin(callback.from_user.id):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)
    text = await _render_pending_text(session)
    await safe_edit_text(callback, text, parse_mode="HTML", reply_markup=_pending_keyboard())


@router.callback_query(F.data.startswith("admin_approve_"))
async def approve_seller(callback: CallbackQuery, session: AsyncSession):
    if not _is_admin(callback.from_user.id):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)

    seller_id = int(callback.data.replace("admin_approve_", ""))
    result = await session.execute(
        select(SellerProfile).options(joinedload(SellerProfile.user)).where(SellerProfile.id == seller_id)
    )
    seller = result.scalars().first()
    if not seller:
        await safe_edit_text(callback, "Seller not found.", reply_markup=_pending_keyboard())
        return

    seller.verified = True
    await session.commit()

    if seller.user and seller.user.telegram_id:
        try:
            await callback.bot.send_message(
                chat_id=int(seller.user.telegram_id),
                text="Your seller account has been verified. You can now create listings.",
            )
        except Exception:
            pass

    await safe_edit_text(
        callback,
        f"Seller {seller.id} approved successfully.",
        reply_markup=_pending_keyboard(),
    )


@router.callback_query(F.data.startswith("admin_reject_"))
async def reject_seller(callback: CallbackQuery, session: AsyncSession):
    if not _is_admin(callback.from_user.id):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)

    seller_id = int(callback.data.replace("admin_reject_", ""))
    result = await session.execute(
        select(SellerProfile).options(joinedload(SellerProfile.user)).where(SellerProfile.id == seller_id)
    )
    seller = result.scalars().first()
    if not seller:
        await safe_edit_text(callback, "Seller not found.", reply_markup=_pending_keyboard())
        return

    seller.verified = False
    await session.commit()

    if seller.user and seller.user.telegram_id:
        try:
            await callback.bot.send_message(
                chat_id=int(seller.user.telegram_id),
                text="Your seller verification was not approved yet. Please review and resubmit your details.",
            )
        except Exception:
            pass

    await safe_edit_text(
        callback,
        f"Seller {seller.id} marked as not approved.",
        reply_markup=_pending_keyboard(),
    )
