"""
Admin handlers for seller verification workflows.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import logging
import re
import secrets
import httpx

from aiogram import F, Router
from aiogram.exceptions import TelegramRetryAfter
from aiogram.filters import Command
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from bot.helpers.telegram import safe_answer_callback, safe_edit_text, safe_replace_with_screen
from core.config import get_settings
from services.escrow import get_escrow_service
from db.models import (
    AdminRole,
    AdminUser,
    CartItem,
    Complaint,
    DisputeStatus,
    Delivery,
    DeliveryAgent,
    DeliveryEvent,
    DeliveryOrder,
    DeliveryStatus,
    Listing,
    NotificationLog,
    Order,
    OrderStatus,
    SellerProfile,
    User,
    WebhookReceipt,
)

router = Router()
settings = get_settings()
logger = logging.getLogger(__name__)
_PUBLIC_IP_CACHE: dict[str, float | str] = {"value": "", "expires_at": 0.0}


async def _ensure_delivery_order_link(session: AsyncSession, delivery_id: int, order_id: int) -> None:
    existing = await session.execute(
        select(DeliveryOrder).where(
            DeliveryOrder.delivery_id == delivery_id,
            DeliveryOrder.order_id == order_id,
        )
    )
    if existing.scalars().first():
        return
    session.add(DeliveryOrder(delivery_id=delivery_id, order_id=order_id, sequence=1))


class AdminStates(StatesGroup):
    awaiting_broadcast_message = State()
    awaiting_privilege_seller_id = State()
    awaiting_privilege_featured = State()
    awaiting_privilege_priority = State()
    awaiting_delivery_agent_name = State()
    awaiting_delivery_agent_phone = State()
    awaiting_delivery_agent_vehicle = State()
    awaiting_delivery_agent_telegram_id = State()
    awaiting_ops_admin_telegram_id = State()


def _is_super_admin_id(telegram_user_id: int) -> bool:
    return bool(settings.admin_telegram_id) and str(telegram_user_id) == str(settings.admin_telegram_id)


async def _admin_role(telegram_user_id: int, session: AsyncSession) -> AdminRole | None:
    if _is_super_admin_id(telegram_user_id):
        return AdminRole.SUPER_ADMIN
    result = await session.execute(select(AdminUser).where(AdminUser.telegram_id == str(telegram_user_id)))
    admin_user = result.scalars().first()
    return admin_user.role if admin_user else None


async def _is_admin(telegram_user_id: int, session: AsyncSession) -> bool:
    return (await _admin_role(telegram_user_id, session)) is not None


async def _is_super_admin(telegram_user_id: int, session: AsyncSession) -> bool:
    role = await _admin_role(telegram_user_id, session)
    return role == AdminRole.SUPER_ADMIN


def _callback_int_suffix(callback_data: str | None, prefix: str) -> int | None:
    payload = (callback_data or "").strip()
    if not payload.startswith(prefix):
        return None
    value = payload.replace(prefix, "", 1).strip()
    if not value.isdigit():
        return None
    return int(value)


def _pending_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Refresh Queue", callback_data="admin_pending_refresh")],
            [InlineKeyboardButton(text="Back to Moderation", callback_data="admin_group_moderation")],
            [InlineKeyboardButton(text="Back to Admin Home", callback_data="admin_tools_open")],
            [InlineKeyboardButton(text="Back to Main Menu", callback_data="back_to_menu")],
        ]
    )


def _actions_keyboard(seller_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Approve Seller", callback_data=f"admin_approve_{seller_id}"),
                InlineKeyboardButton(text="Reject Seller", callback_data=f"admin_reject_{seller_id}"),
            ],
            [InlineKeyboardButton(text="Back to Approval Queue", callback_data="admin_pending_refresh")],
        ]
    )


def _admin_tools_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Overview & Health", callback_data="admin_group_overview")],
            [
                InlineKeyboardButton(text="Commerce Operations", callback_data="admin_group_commerce"),
                InlineKeyboardButton(text="Delivery Operations", callback_data="admin_group_delivery"),
            ],
            [
                InlineKeyboardButton(text="Moderation & Risk", callback_data="admin_group_moderation"),
                InlineKeyboardButton(text="System & Access", callback_data="admin_group_system"),
            ],
            [InlineKeyboardButton(text="Danger Zone", callback_data="admin_danger_tools")],
            [InlineKeyboardButton(text="Back to Main Menu", callback_data="back_to_menu")],
        ]
    )


def _admin_group_overview_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Platform Stats", callback_data="admin_stats"),
                InlineKeyboardButton(text="Flow Health", callback_data="admin_flow_health"),
            ],
            [InlineKeyboardButton(text="Back to Admin Home", callback_data="admin_tools_open")],
            [InlineKeyboardButton(text="Back to Main Menu", callback_data="back_to_menu")],
        ]
    )


def _admin_group_commerce_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Transactions", callback_data="admin_transactions"),
                InlineKeyboardButton(text="Transaction Logs", callback_data="admin_transaction_logs"),
            ],
            [
                InlineKeyboardButton(text="Payout Monitor", callback_data="admin_payouts"),
                InlineKeyboardButton(text="Listings Catalog", callback_data="admin_listings"),
            ],
            [InlineKeyboardButton(text="Vendors", callback_data="admin_vendors")],
            [InlineKeyboardButton(text="Back to Admin Home", callback_data="admin_tools_open")],
            [InlineKeyboardButton(text="Back to Main Menu", callback_data="back_to_menu")],
        ]
    )


def _admin_group_delivery_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Delivery Tracker", callback_data="admin_delivery_tracking"),
                InlineKeyboardButton(text="Assignment Queue", callback_data="admin_delivery_assign_picker"),
            ],
            [
                InlineKeyboardButton(text="Agents", callback_data="admin_delivery_agents"),
                InlineKeyboardButton(text="Register Agent", callback_data="admin_delivery_agent_add"),
            ],
            [InlineKeyboardButton(text="Delivery Logs", callback_data="admin_transaction_logs")],
            [InlineKeyboardButton(text="Back to Admin Home", callback_data="admin_tools_open")],
            [InlineKeyboardButton(text="Back to Main Menu", callback_data="back_to_menu")],
        ]
    )


def _admin_group_moderation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Seller Approvals", callback_data="admin_pending_refresh"),
                InlineKeyboardButton(text="Complaints + IP", callback_data="admin_complaints_ip"),
            ],
            [InlineKeyboardButton(text="Vendor Privileges", callback_data="admin_privileges_help")],
            [InlineKeyboardButton(text="Back to Admin Home", callback_data="admin_tools_open")],
            [InlineKeyboardButton(text="Back to Main Menu", callback_data="back_to_menu")],
        ]
    )


def _admin_group_system_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Broadcast Center", callback_data="admin_broadcast_help"),
                InlineKeyboardButton(text="Ops Admin Access", callback_data="admin_ops_admins"),
            ],
            [InlineKeyboardButton(text="User Directory", callback_data="admin_users")],
            [InlineKeyboardButton(text="Back to Admin Home", callback_data="admin_tools_open")],
            [InlineKeyboardButton(text="Back to Main Menu", callback_data="back_to_menu")],
        ]
    )


def _admin_transaction_logs_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Successful Payments", callback_data="admin_txlog_success"),
                InlineKeyboardButton(text="Failed Transactions", callback_data="admin_txlog_failed"),
            ],
            [
                InlineKeyboardButton(text="Delivered Orders", callback_data="admin_txlog_delivered"),
                InlineKeyboardButton(text="Awaiting Delivery", callback_data="admin_txlog_awaiting"),
            ],
            [InlineKeyboardButton(text="Back to Commerce", callback_data="admin_group_commerce")],
            [InlineKeyboardButton(text="Back to Admin Home", callback_data="admin_tools_open")],
            [InlineKeyboardButton(text="Back to Main Menu", callback_data="back_to_menu")],
        ]
    )


def _danger_tools_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Delete Listing Record", callback_data="admin_delete_help_listing")],
            [InlineKeyboardButton(text="Delete Vendor Profile", callback_data="admin_delete_help_vendor")],
            [InlineKeyboardButton(text="Delete User Account", callback_data="admin_delete_help_user")],
            [InlineKeyboardButton(text="Clear Transactions + Listings", callback_data="admin_wipe_marketplace_confirm")],
            [InlineKeyboardButton(text="Back to Admin Home", callback_data="admin_tools_open")],
            [InlineKeyboardButton(text="Back to Main Menu", callback_data="back_to_menu")],
        ]
    )


def _delete_help_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Back to Admin Home", callback_data="admin_tools_open")],
            [InlineKeyboardButton(text="Back to Main Menu", callback_data="back_to_menu")],
        ]
    )


def _delete_picker_keyboard(items: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=label, callback_data=cb)] for label, cb in items]
    rows.append([InlineKeyboardButton(text="Back to Admin Home", callback_data="admin_tools_open")])
    rows.append([InlineKeyboardButton(text="Back to Main Menu", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _wipe_marketplace_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Yes, clear now", callback_data="admin_wipe_marketplace_execute")],
            [InlineKeyboardButton(text="Cancel", callback_data="admin_danger_tools")],
        ]
    )


def _wipe_marketplace_done_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Back to Admin Home", callback_data="admin_tools_open")],
            [InlineKeyboardButton(text="Back to Danger Zone", callback_data="admin_danger_tools")],
        ]
    )


async def _wipe_transactions_and_listings(session: AsyncSession) -> dict[str, int]:
    """
    Delete all transaction/listing data while preserving users/admin/agents/seller profiles.
    """
    counts: dict[str, int] = {}
    delete_plan: list[tuple[str, object]] = [
        ("delivery_events", DeliveryEvent),
        ("delivery_orders", DeliveryOrder),
        ("deliveries", Delivery),
        ("complaints", Complaint),
        ("orders", Order),
        ("cart_items", CartItem),
        ("listings", Listing),
        ("webhook_receipts", WebhookReceipt),
    ]
    for label, model in delete_plan:
        result = await session.execute(delete(model))
        counts[label] = int(result.rowcount or 0)
    return counts


async def _find_seller_by_identifier(
    session: AsyncSession,
    identifier: str,
    with_user: bool = False,
) -> SellerProfile | None:
    ident = (identifier or "").strip()
    query = select(SellerProfile)
    if with_user:
        query = query.options(joinedload(SellerProfile.user))

    if ident.isdigit():
        seller = await session.get(SellerProfile, int(ident))
        if seller:
            if with_user:
                result = await session.execute(
                    select(SellerProfile)
                    .options(joinedload(SellerProfile.user))
                    .where(SellerProfile.id == seller.id)
                )
                return result.scalars().first()
            return seller

    result = await session.execute(query.where(SellerProfile.seller_code == ident.upper()))
    return result.scalars().first()


async def _find_listing_by_identifier(session: AsyncSession, identifier: str) -> Listing | None:
    ident = (identifier or "").strip()
    if ident.isdigit():
        listing = await session.get(Listing, int(ident))
        if listing:
            return listing
    result = await session.execute(select(Listing).where(Listing.listing_code == ident.upper()))
    return result.scalars().first()


async def _resolve_public_egress_ip() -> str:
    now = asyncio.get_running_loop().time()
    cached_value = str(_PUBLIC_IP_CACHE.get("value") or "").strip()
    cached_expires = float(_PUBLIC_IP_CACHE.get("expires_at") or 0.0)
    if cached_value and now < cached_expires:
        return cached_value

    endpoints = (
        "https://api.ipify.org",
        "https://ifconfig.me/ip",
        "https://ipv4.icanhazip.com",
    )
    async with httpx.AsyncClient(timeout=3.0) as client:
        for endpoint in endpoints:
            try:
                response = await client.get(endpoint)
                if response.status_code != 200:
                    continue
                ip = response.text.strip()
                if not ip:
                    continue
                _PUBLIC_IP_CACHE["value"] = ip
                _PUBLIC_IP_CACHE["expires_at"] = now + 120
                return ip
            except Exception:
                continue

    return "Unavailable"


def _broadcast_audience_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="All Users", callback_data="admin_broadcast_audience_all")],
            [InlineKeyboardButton(text="Buyers Only", callback_data="admin_broadcast_audience_buyers")],
            [InlineKeyboardButton(text="Sellers Only", callback_data="admin_broadcast_audience_sellers")],
            [
                InlineKeyboardButton(
                    text="Verified Sellers",
                    callback_data="admin_broadcast_audience_verified_sellers",
                )
            ],
            [InlineKeyboardButton(text="Back to System", callback_data="admin_group_system")],
            [InlineKeyboardButton(text="Back to Admin Home", callback_data="admin_tools_open")],
        ]
    )


def _privilege_featured_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Featured: ON", callback_data="admin_priv_featured_1")],
            [InlineKeyboardButton(text="Featured: OFF", callback_data="admin_priv_featured_0")],
            [InlineKeyboardButton(text="Back to Moderation", callback_data="admin_group_moderation")],
            [InlineKeyboardButton(text="Back to Admin Home", callback_data="admin_tools_open")],
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
        name = seller.full_name or (f"{user.first_name} {user.last_name or ''}".strip() if user else "Unknown")
        username = f"@{user.username}" if user and user.username else "N/A"
        text += (
            f"<b>Seller ID:</b> {seller.seller_code}\n"
            f"<b>Name:</b> {name}\n"
            f"<b>Level:</b> {seller.level or 'N/A'}\n"
            f"<b>Username:</b> {username}\n"
        )
        text += (
            f"<b>Student:</b> {'Yes' if seller.is_student else 'No'}\n"
            f"<b>Email:</b> {seller.student_email or 'N/A'}\n"
            f"<b>Bank:</b> {(seller.bank_name or seller.bank_code)} ({seller.bank_code}) / {seller.account_number}\n"
            f"<b>Account Name:</b> {seller.account_name}\n"
            f"<b>ID Doc:</b> {seller.id_document_url or 'N/A'}\n"
            f"<b>Submitted:</b> {seller.created_at.strftime('%Y-%m-%d %H:%M')}\n"
            f"/review_seller_{seller.seller_code}\n\n"
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


async def _render_flow_health_text(session: AsyncSession) -> str:
    now = datetime.now(timezone.utc)
    since_24h = now - timedelta(hours=24)
    stuck_before = now - timedelta(hours=3)

    failed_callbacks_result = await session.execute(
        select(func.count(NotificationLog.id))
        .where(NotificationLog.event_type == "callback_error")
        .where(NotificationLog.status == "failed")
        .where(NotificationLog.created_at >= since_24h)
    )
    failed_callbacks = int(failed_callbacks_result.scalar() or 0)

    reminder_sent_result = await session.execute(
        select(func.count(NotificationLog.id))
        .where(NotificationLog.event_type == "delivery_confirmation_reminder")
        .where(NotificationLog.status == "sent")
        .where(NotificationLog.created_at >= since_24h)
    )
    reminders_sent = int(reminder_sent_result.scalar() or 0)

    payout_errors_result = await session.execute(
        select(func.count(Order.id))
        .where(Order.seller_payout_ref.is_not(None))
        .where(
            Order.seller_payout_status.in_(
                ("failed", "error", "cancelled", "declined", "reversed", "not_authorized")
            )
        )
    )
    payout_errors = int(payout_errors_result.scalar() or 0)

    stuck_deliveries_result = await session.execute(
        select(func.count(Delivery.id))
        .where(Delivery.status.in_((DeliveryStatus.ASSIGNED, DeliveryStatus.PICKED_UP, DeliveryStatus.IN_TRANSIT)))
        .where(Delivery.updated_at < stuck_before)
    )
    stuck_deliveries = int(stuck_deliveries_result.scalar() or 0)

    unresolved_disputes_result = await session.execute(
        select(func.count(Complaint.id)).where(
            Complaint.status.in_((DisputeStatus.OPEN, DisputeStatus.INVESTIGATING))
        )
    )
    unresolved_disputes = int(unresolved_disputes_result.scalar() or 0)

    return (
        "<b>Flow Health</b>\n\n"
        f"<b>Failed Callbacks (24h):</b> {failed_callbacks}\n"
        f"<b>Payout Errors (open):</b> {payout_errors}\n"
        f"<b>Stuck Deliveries (&gt;3h):</b> {stuck_deliveries}\n"
        f"<b>Unresolved Disputes:</b> {unresolved_disputes}\n"
        f"<b>Reminder Notifications Sent (24h):</b> {reminders_sent}\n\n"
        "<b>Targets</b>\n"
        "- Failed callbacks: 0\n"
        "- Payout errors: 0\n"
        "- Stuck deliveries: 0\n"
        "- Unresolved disputes: as low as possible"
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
            f"<b>Seller ID:</b> {seller.seller_code}\n"
            f"<b>Name:</b> {user.first_name} {user.last_name or ''}\n"
            f"<b>Verified:</b> {'Yes' if seller.verified else 'No'}\n"
            f"<b>Featured:</b> {'Yes' if seller.is_featured else 'No'} "
            f"(priority {seller.priority_score})\n"
            f"<b>Listings:</b> {listings_count} | <b>Tx:</b> {transactions_count}\n\n"
        )
    return text


async def _render_users_text(session: AsyncSession, limit: int = 50) -> str:
    total_result = await session.execute(select(func.count(User.id)))
    total_users = int(total_result.scalar() or 0)

    result = await session.execute(
        select(User)
        .order_by(User.created_at.desc())
        .limit(limit)
    )
    users = result.scalars().all()
    if not users:
        return "<b>User Directory</b>\n\nNo users found."

    text = (
        "<b>User Directory</b>\n\n"
        f"<b>Total Users:</b> {total_users}\n"
        f"<b>Showing:</b> latest {len(users)}\n\n"
    )
    for user in users:
        full_name = f"{user.first_name} {user.last_name or ''}".strip()
        username_line = f"Username: @{user.username}\n" if user.username else "Username: N/A\n"
        text += (
            f"<b>User #{user.id}</b>\n"
            f"Name: {full_name}\n"
            f"Telegram ID: <code>{user.telegram_id}</code>\n"
            f"{username_line}"
        )
        text += f"Joined: {user.created_at.strftime('%Y-%m-%d %H:%M')}\n\n"
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


async def _render_payouts_text(session: AsyncSession, limit: int = 15) -> str:
    result = await session.execute(
        select(Order)
        .options(
            joinedload(Order.seller).joinedload(SellerProfile.user),
            joinedload(Order.buyer),
        )
        .order_by(Order.updated_at.desc())
        .limit(limit)
    )
    orders = result.scalars().all()
    if not orders:
        return "<b>Payout Monitor</b>\n\nNo orders found."

    text = "<b>Payout Monitor (latest 15)</b>\n\n"
    for order in orders:
        if not (order.seller_payout_ref or order.seller_payout_status or order.status in {OrderStatus.COMPLETED, OrderStatus.PAID}):
            continue
        seller_name = order.seller.user.first_name if order.seller and order.seller.user else "Unknown"
        buyer_name = order.buyer.first_name if order.buyer else "Unknown"
        payout_status = order.seller_payout_status or "N/A"
        if payout_status == "not_authorized":
            payout_status = "not_authorized (enable NGN bank_account payout API on Korapay)"

        text += (
            f"<b>Order #{order.id}</b>\n"
            f"Order status: {order.status.value}\n"
            f"Payout ref: {order.seller_payout_ref or 'N/A'}\n"
            f"Payout status: {payout_status}\n"
            f"Attempted at: {order.seller_payout_attempted_at.strftime('%Y-%m-%d %H:%M') if order.seller_payout_attempted_at else 'N/A'}\n"
            f"Buyer: {buyer_name} | Seller: {seller_name}\n\n"
        )
    if text.strip() == "<b>Payout Monitor (latest 15)</b>":
        return "<b>Payout Monitor</b>\n\nNo payout records yet."
    return text


async def _render_transaction_logs_text(session: AsyncSession, log_type: str, limit: int = 20) -> str:
    query = (
        select(Order)
        .options(
            joinedload(Order.buyer),
            joinedload(Order.seller).joinedload(SellerProfile.user),
            joinedload(Order.listing),
            joinedload(Order.delivery),
        )
        .order_by(Order.updated_at.desc())
        .limit(limit)
    )

    title = "Transaction Logs"
    if log_type == "success":
        title = "Transaction Logs - Successful"
        query = query.where(Order.status.in_((OrderStatus.PAID, OrderStatus.COMPLETED)))
    elif log_type == "failed":
        title = "Transaction Logs - Failed"
        query = query.where(Order.status.in_((OrderStatus.CANCELLED, OrderStatus.REFUNDED)))
    elif log_type == "delivered":
        title = "Transaction Logs - Delivered"
        query = query.where(
            (Order.status == OrderStatus.COMPLETED)
            | (Order.delivered_at.is_not(None))
        )
    elif log_type == "awaiting":
        title = "Transaction Logs - Awaiting Delivery"
        query = query.where(Order.status == OrderStatus.PAID).where(Order.delivered_at.is_(None))

    result = await session.execute(query)
    orders = result.scalars().all()
    if not orders:
        return f"<b>{title}</b>\n\nNo records found."

    text = f"<b>{title} (latest {limit})</b>\n\n"
    for order in orders:
        buyer_name = order.buyer.first_name if order.buyer else "Unknown"
        seller_name = order.seller.user.first_name if order.seller and order.seller.user else "Unknown"
        listing_title = order.listing.title if order.listing else "Unknown listing"
        delivery_status = order.delivery.status.value if order.delivery else "N/A"
        text += (
            f"<b>Order #{order.id}</b>\n"
            f"Status: {order.status.value}\n"
            f"Amount: {order.amount}\n"
            f"Tx Ref: {order.transaction_ref or 'N/A'}\n"
            f"Delivery: {delivery_status}\n"
            f"Buyer: {buyer_name} | Seller: {seller_name}\n"
            f"Listing: {listing_title[:40]}\n"
            f"Updated: {order.updated_at.strftime('%Y-%m-%d %H:%M')}\n\n"
        )
    return text


async def _render_complaints_ip_text(session: AsyncSession, limit: int = 10) -> str:
    total_result = await session.execute(select(func.count(Complaint.id)))
    total = total_result.scalar() or 0

    open_result = await session.execute(
        select(func.count(Complaint.id)).where(Complaint.status == DisputeStatus.OPEN)
    )
    open_count = open_result.scalar() or 0

    investigating_result = await session.execute(
        select(func.count(Complaint.id)).where(Complaint.status == DisputeStatus.INVESTIGATING)
    )
    investigating_count = investigating_result.scalar() or 0

    result = await session.execute(
        select(Complaint)
        .options(
            joinedload(Complaint.order).joinedload(Order.listing),
            joinedload(Complaint.complainant),
        )
        .order_by(Complaint.created_at.desc())
        .limit(limit)
    )
    complaints = result.scalars().all()

    public_ip = await _resolve_public_egress_ip()

    text = (
        "<b>Complaints Dashboard</b>\n\n"
        "<b>IP Diagnostics</b>\n"
        f"Public Egress IP: <code>{public_ip}</code>\n\n"
        "<b>Complaint Queue</b>\n"
        f"Total: {total}\n"
        f"Open: {open_count}\n"
        f"Investigating: {investigating_count}\n\n"
    )

    if not complaints:
        return text + "No complaints found."

    text += "<b>Latest Complaints</b>\n"
    for complaint in complaints:
        listing_title = (
            complaint.order.listing.title
            if complaint.order and complaint.order.listing
            else "Unknown item"
        )
        complainant_name = complaint.complainant.first_name if complaint.complainant else "Unknown"
        text += (
            f"\n#{complaint.id} | Order #{complaint.order_id} | {complaint.status.value}\n"
            f"By: {complainant_name}\n"
            f"Item: {listing_title[:32]}\n"
            f"Subject: {complaint.subject[:48]}\n"
        )

    return text


async def _fetch_recent_complaints(session: AsyncSession, limit: int = 8) -> list[Complaint]:
    result = await session.execute(
        select(Complaint)
        .options(
            joinedload(Complaint.order).joinedload(Order.listing),
            joinedload(Complaint.complainant),
        )
        .order_by(Complaint.created_at.desc())
        .limit(limit)
    )
    return result.scalars().all()


def _complaints_dashboard_keyboard(complaints: list[Complaint]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for complaint in complaints:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"View #{complaint.id}",
                    callback_data=f"admin_complaint_view_{complaint.id}",
                )
            ]
        )
        if complaint.status == DisputeStatus.OPEN:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"#{complaint.id} Investigating",
                        callback_data=f"admin_complaint_set_{complaint.id}_I",
                    ),
                    InlineKeyboardButton(
                        text=f"#{complaint.id} Resolved",
                        callback_data=f"admin_complaint_set_{complaint.id}_R",
                    ),
                ]
            )
        elif complaint.status == DisputeStatus.INVESTIGATING:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"#{complaint.id} Resolved",
                        callback_data=f"admin_complaint_set_{complaint.id}_R",
                    ),
                    InlineKeyboardButton(
                        text=f"#{complaint.id} Closed",
                        callback_data=f"admin_complaint_set_{complaint.id}_C",
                    ),
                ]
            )

    rows.append([InlineKeyboardButton(text="Refresh Complaints", callback_data="admin_complaints_ip")])
    rows.append([InlineKeyboardButton(text="Back to Moderation", callback_data="admin_group_moderation")])
    rows.append([InlineKeyboardButton(text="Back to Admin Home", callback_data="admin_tools_open")])
    rows.append([InlineKeyboardButton(text="Back to Main Menu", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _complaint_detail_keyboard(complaint_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Set Investigating",
                    callback_data=f"admin_complaint_set_{complaint_id}_I",
                ),
                InlineKeyboardButton(
                    text="Set Resolved",
                    callback_data=f"admin_complaint_set_{complaint_id}_R",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Mark Closed",
                    callback_data=f"admin_complaint_set_{complaint_id}_C",
                )
            ],
            [InlineKeyboardButton(text="Back to Complaints", callback_data="admin_complaints_ip")],
        ]
    )


async def _payouts_keyboard(session: AsyncSession) -> InlineKeyboardMarkup:
    failed_statuses = {"failed", "error", "cancelled", "reversed", "declined", "not_authorized"}
    result = await session.execute(
        select(Order)
        .where(Order.seller_payout_ref.is_not(None))
        .where(Order.seller_payout_status.in_(tuple(failed_statuses)))
        .order_by(Order.updated_at.desc())
        .limit(6)
    )
    failed_orders = result.scalars().all()

    rows: list[list[InlineKeyboardButton]] = []
    for order in failed_orders:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"Retry payout #{order.id}",
                    callback_data=f"admin_retry_payout_{order.id}",
                )
            ]
        )

    rows.append([InlineKeyboardButton(text="Refresh Payouts", callback_data="admin_payouts")])
    rows.append([InlineKeyboardButton(text="Back to Commerce", callback_data="admin_group_commerce")])
    rows.append([InlineKeyboardButton(text="Back to Admin Home", callback_data="admin_tools_open")])
    rows.append([InlineKeyboardButton(text="Back to Main Menu", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
        "<code>/delete_listing_&lt;listing_code_or_id&gt;</code>\n\n"
    )
    for listing in listings:
        seller_name = listing.seller.user.first_name if listing.seller and listing.seller.user else "Unknown"
        text += (
            f"<b>Listing ID:</b> {listing.listing_code}\n"
            f"<b>Title:</b> {listing.title}\n"
            f"<b>Category:</b> {listing.category.value}\n"
            f"<b>Quantity:</b> {listing.quantity}\n"
            f"<b>Available:</b> {'Yes' if listing.available else 'No'}\n"
            f"<b>Seller:</b> {seller_name}\n\n"
        )
    return text


async def _render_delivery_agents_text(session: AsyncSession, limit: int = 20) -> str:
    result = await session.execute(
        select(DeliveryAgent)
        .order_by(DeliveryAgent.created_at.desc())
        .limit(limit)
    )
    agents = result.scalars().all()
    if not agents:
        return "<b>Delivery Agents</b>\n\nNo delivery agents found."

    text = "<b>Delivery Agents</b>\n\n"
    for agent in agents:
        text += (
            f"<b>Agent #{agent.id}</b>\n"
            f"Name: {agent.name}\n"
            f"Phone: {agent.phone or 'N/A'}\n"
            f"Vehicle: {agent.vehicle_type or 'N/A'}\n"
            f"Active: {'Yes' if agent.is_active else 'No'}\n\n"
        )
    return text


async def _render_delivery_tracking_text(session: AsyncSession, limit: int = 20) -> str:
    result = await session.execute(
        select(Delivery)
        .options(
            joinedload(Delivery.agent),
            joinedload(Delivery.order).joinedload(Order.buyer),
            joinedload(Delivery.order).joinedload(Order.seller).joinedload(SellerProfile.user),
        )
        .order_by(Delivery.updated_at.desc())
        .limit(limit)
    )
    deliveries = result.scalars().all()
    if not deliveries:
        return "<b>Delivery Tracking</b>\n\nNo deliveries found."

    text = "<b>Delivery Tracking (latest 20)</b>\n\n"
    for delivery in deliveries:
        order = delivery.order
        buyer_name = order.buyer.first_name if order and order.buyer else "Unknown"
        seller_name = (
            order.seller.user.first_name if order and order.seller and order.seller.user else "Unknown"
        )
        agent_name = delivery.agent.name if delivery.agent else "Unassigned"
        text += (
            f"<b>Delivery #{delivery.id}</b> | Order #{delivery.order_id}\n"
            f"Status: {delivery.status.value}\n"
            f"Agent: {agent_name}\n"
            f"Buyer: {buyer_name} | Seller: {seller_name}\n"
            f"Updated: {delivery.updated_at.strftime('%Y-%m-%d %H:%M')}\n\n"
        )
    return text


async def _render_ops_admins_text(session: AsyncSession, limit: int = 50) -> str:
    result = await session.execute(
        select(AdminUser).order_by(AdminUser.created_at.desc()).limit(limit)
    )
    admins = result.scalars().all()
    text = "<b>Ops Admin Access</b>\n\n"
    if not admins:
        text += "No ops admins added yet.\n"
    else:
        for admin_user in admins:
            text += (
                f"ID: <code>{admin_user.telegram_id}</code>\n"
                f"Role: {admin_user.role.value}\n"
                f"Added: {admin_user.created_at.strftime('%Y-%m-%d %H:%M')}\n\n"
            )
    text += f"Super Admin ID: <code>{settings.admin_telegram_id or 'N/A'}</code>"
    return text


def _ops_admins_keyboard(admins: list[AdminUser]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    rows.append([InlineKeyboardButton(text="Add Ops Admin", callback_data="admin_ops_add")])
    for admin_user in admins:
        if admin_user.role == AdminRole.OPS_ADMIN:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"Remove {admin_user.telegram_id}",
                        callback_data=f"admin_ops_remove_{admin_user.id}",
                    )
                ]
            )
    rows.append([InlineKeyboardButton(text="Back to System", callback_data="admin_group_system")])
    rows.append([InlineKeyboardButton(text="Back to Admin Home", callback_data="admin_tools_open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _delivery_assign_pick_keyboard(deliveries: list[Delivery]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for delivery in deliveries:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"Assign Job #{delivery.id}",
                    callback_data=f"admin_delivery_assign_{delivery.id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="Open Delivery Tracker", callback_data="admin_delivery_tracking")])
    rows.append([InlineKeyboardButton(text="Back to Delivery", callback_data="admin_group_delivery")])
    rows.append([InlineKeyboardButton(text="Back to Admin Home", callback_data="admin_tools_open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _delivery_assign_agent_keyboard(delivery_id: int, agents: list[DeliveryAgent]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for agent in agents:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{agent.name} (#{agent.id})",
                    callback_data=f"admin_delivery_set_{delivery_id}_{agent.id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="Back to Assign Delivery", callback_data="admin_delivery_assign_picker")])
    rows.append([InlineKeyboardButton(text="Back to Delivery", callback_data="admin_group_delivery")])
    rows.append([InlineKeyboardButton(text="Back to Admin Home", callback_data="admin_tools_open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _delivery_agents_keyboard(agents: list[DeliveryAgent]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for agent in agents:
        if agent.is_active:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"Deactivate {agent.name} (#{agent.id})",
                        callback_data=f"admin_delivery_agent_deactivate_{agent.id}",
                    )
                ]
            )
        else:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"Activate {agent.name} (#{agent.id})",
                        callback_data=f"admin_delivery_agent_activate_{agent.id}",
                    )
                ]
            )
    rows.append([InlineKeyboardButton(text="Refresh Agents", callback_data="admin_delivery_agents")])
    rows.append([InlineKeyboardButton(text="Back to Delivery", callback_data="admin_group_delivery")])
    rows.append([InlineKeyboardButton(text="Back to Admin Home", callback_data="admin_tools_open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _get_broadcast_recipients(session: AsyncSession, audience: str) -> list[User]:
    if audience == "all":
        recipients_query = select(User).where(User.telegram_id.is_not(None)).distinct()
    elif audience == "buyers":
        recipients_query = (
            select(User)
            .join(Order, Order.buyer_id == User.id)
            .where(User.telegram_id.is_not(None))
            .distinct()
        )
    elif audience == "sellers":
        recipients_query = (
            select(User)
            .join(SellerProfile, SellerProfile.user_id == User.id)
            .where(User.telegram_id.is_not(None))
            .distinct()
        )
    else:
        recipients_query = (
            select(User)
            .join(SellerProfile, SellerProfile.user_id == User.id)
            .where(SellerProfile.verified.is_(True))
            .where(User.telegram_id.is_not(None))
            .distinct()
        )

    recipient_result = await session.execute(recipients_query)
    return recipient_result.scalars().all()


def _personalize_broadcast_template(template: str, user: User) -> str:
    first_name = (user.first_name or "").strip() or "there"
    return template.replace("{{first_name}}", first_name)


async def _send_broadcast(
    bot,
    recipients: list[User],
    message_text: str | None = None,
    photo_file_id: str | None = None,
    caption: str | None = None,
) -> tuple[int, int]:
    sent = 0
    failed = 0
    for user in recipients:
        try:
            chat_id = int(str(user.telegram_id))
            if photo_file_id:
                personalized_caption = (
                    _personalize_broadcast_template(caption, user) if caption else None
                )
                await bot.send_photo(chat_id=chat_id, photo=photo_file_id, caption=personalized_caption)
            else:
                personalized_text = _personalize_broadcast_template(message_text or "", user)
                await bot.send_message(chat_id=chat_id, text=personalized_text)
            sent += 1
        except TelegramRetryAfter as exc:
            try:
                await asyncio.sleep(exc.retry_after)
                if photo_file_id:
                    personalized_caption = (
                        _personalize_broadcast_template(caption, user) if caption else None
                    )
                    await bot.send_photo(chat_id=chat_id, photo=photo_file_id, caption=personalized_caption)
                else:
                    personalized_text = _personalize_broadcast_template(message_text or "", user)
                    await bot.send_message(chat_id=chat_id, text=personalized_text)
                sent += 1
            except Exception:
                failed += 1
        except Exception:
            failed += 1
    return sent, failed


@router.message(Command("admin_tools"))
@router.message(Command("admin"))
async def admin_tools(message: Message, state: FSMContext, session: AsyncSession):
    if not await _is_admin(message.from_user.id, session):
        await message.reply("You are not authorized to use this command.")
        return
    await state.clear()

    text = (
        "<b>Admin Tools</b>\n\n"
        "Use grouped sections for faster navigation:\n"
        "- Overview & Health\n"
        "- Commerce Operations\n"
        "- Delivery Operations\n"
        "- Moderation & Risk\n"
        "- System & Access\n\n"
        "Transaction log filters:\n"
        "- Successful\n"
        "- Failed\n"
        "- Delivered\n"
        "- Awaiting Delivery\n\n"
        "Delete commands:\n"
        "<code>/delete_listing_LST-ABC12345</code>\n"
        "<code>/delete_vendor_SEL-ABC12345</code>\n"
        "<code>/delete_user_67</code>"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=_admin_tools_keyboard())


@router.message(Command("admin_users"))
async def admin_users_command(message: Message, session: AsyncSession):
    if not await _is_admin(message.from_user.id, session):
        await message.reply("You are not authorized to use this command.")
        return
    text = await _render_users_text(session)
    await message.answer(text, parse_mode="HTML", reply_markup=_admin_group_system_keyboard())


@router.message(Command("pending_sellers"))
async def pending_sellers(message: Message, session: AsyncSession):
    if not await _is_admin(message.from_user.id, session):
        await message.reply("You are not authorized to use this command.")
        return

    text = await _render_pending_text(session)
    await message.answer(text, parse_mode="HTML", reply_markup=_pending_keyboard())


@router.message(F.text.regexp(r"^/review_seller_([A-Za-z0-9\-]+)$"))
async def review_seller(message: Message, session: AsyncSession):
    if not await _is_admin(message.from_user.id, session):
        await message.reply("You are not authorized to use this command.")
        return

    command_text = (message.text or "").strip()
    seller_identifier = command_text.split("_")[-1] if command_text else ""
    seller = await _find_seller_by_identifier(session, seller_identifier, with_user=True)
    if not seller:
        await message.reply("Seller not found.")
        return

    user = seller.user
    name = seller.full_name or (f"{user.first_name} {user.last_name or ''}".strip() if user else "Unknown")
    username = f"@{user.username}" if user and user.username else "N/A"
    text = (
        "<b>Seller Verification Review</b>\n\n"
        f"<b>Seller ID:</b> {seller.seller_code}\n"
        f"<b>Name:</b> {name}\n"
        f"<b>Level:</b> {seller.level or 'N/A'}\n"
        f"<b>Username:</b> {username}\n"
        f"<b>Student:</b> {'Yes' if seller.is_student else 'No'}\n"
        f"<b>Featured Vendor:</b> {'Yes' if seller.is_featured else 'No'}\n"
        f"<b>Priority Score:</b> {seller.priority_score}\n"
        f"<b>Student Email:</b> {seller.student_email or 'N/A'}\n"
        f"<b>Hall:</b> {seller.hall or 'N/A'}\n"
        f"<b>Room Number:</b> {seller.room_number or 'N/A'}\n"
        f"<b>Address:</b> {seller.address or 'N/A'}\n"
        f"<b>Bank:</b> {(seller.bank_name or seller.bank_code)}\n"
        f"<b>Bank Code:</b> {seller.bank_code}\n"
        f"<b>Account Number:</b> {seller.account_number}\n"
        f"<b>Account Name:</b> {seller.account_name}\n"
        f"<b>ID Document File:</b> {seller.id_document_url or 'N/A'}"
    )
    if seller.id_document_url:
        try:
            await message.answer_photo(
                photo=seller.id_document_url,
                caption=text,
                parse_mode="HTML",
                reply_markup=_actions_keyboard(seller.id),
            )
            return
        except Exception:
            pass

    await message.answer(text, parse_mode="HTML", reply_markup=_actions_keyboard(seller.id))


@router.message(F.text.regexp(r"^/delete_listing_([A-Za-z0-9\-]+)$"))
async def delete_listing_by_command(message: Message, session: AsyncSession):
    if not await _is_admin(message.from_user.id, session):
        await message.reply("You are not authorized to use this command.")
        return

    command_text = (message.text or "").strip()
    listing_identifier = command_text.split("_")[-1] if command_text else ""
    listing = await _find_listing_by_identifier(session, listing_identifier)
    if not listing:
        await message.reply("Listing not found.")
        return

    order_count_result = await session.execute(
        select(func.count(Order.id)).where(Order.listing_id == listing.id)
    )
    order_count = order_count_result.scalar() or 0
    if order_count > 0:
        await message.reply("Cannot delete listing with existing transactions.")
        return

    await session.delete(listing)
    await session.commit()
    await message.reply(f"Listing {listing.listing_code} deleted.")


@router.message(F.text.regexp(r"^/delete_vendor_([A-Za-z0-9\-]+)$"))
async def delete_vendor_by_command(message: Message, session: AsyncSession):
    if not await _is_admin(message.from_user.id, session):
        await message.reply("You are not authorized to use this command.")
        return

    command_text = (message.text or "").strip()
    seller_identifier = command_text.split("_")[-1] if command_text else ""
    seller = await _find_seller_by_identifier(session, seller_identifier)
    if not seller:
        await message.reply("Vendor not found.")
        return

    order_count_result = await session.execute(
        select(func.count(Order.id)).where(Order.seller_id == seller.id)
    )
    order_count = order_count_result.scalar() or 0
    if order_count > 0:
        await message.reply("Cannot delete vendor with existing transactions.")
        return

    await session.delete(seller)
    await session.commit()
    await message.reply(f"Vendor {seller.seller_code} deleted.")


@router.message(F.text.regexp(r"^/delete_user_(\d+)$"))
async def delete_user_by_command(message: Message, session: AsyncSession):
    if not await _is_admin(message.from_user.id, session):
        await message.reply("You are not authorized to use this command.")
        return

    command_text = (message.text or "").strip()
    user_id = int(command_text.split("_")[-1])
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


@router.message(F.text.regexp(r"^/set_vendor_privilege_([A-Za-z0-9\-]+)_(0|1)_(\d{1,3})$"))
async def set_vendor_privilege(message: Message, session: AsyncSession):
    if not await _is_admin(message.from_user.id, session):
        await message.reply("You are not authorized to use this command.")
        return

    match = re.match(r"^/set_vendor_privilege_([A-Za-z0-9\-]+)_(0|1)_(\d{1,3})$", message.text or "")
    if not match:
        await message.reply("Invalid command format.")
        return

    seller_identifier, featured_text, priority_text = match.groups()
    is_featured = featured_text == "1"
    priority_score = int(priority_text)

    if priority_score > 100:
        await message.reply("Priority must be between 0 and 100.")
        return

    seller = await _find_seller_by_identifier(session, seller_identifier)
    if not seller:
        await message.reply("Vendor not found.")
        return

    seller.is_featured = is_featured
    seller.priority_score = priority_score
    await session.commit()
    await message.reply(
        f"Vendor {seller.seller_code} updated.\n"
        f"Featured: {'Yes' if is_featured else 'No'}\nPriority: {priority_score}"
    )


@router.message(F.text.regexp(r"^/broadcast_(all|buyers|sellers|verified_sellers)\s+([\s\S]+)$"))
async def broadcast_from_admin_chat(message: Message, session: AsyncSession):
    if not await _is_admin(message.from_user.id, session):
        await message.reply("You are not authorized to use this command.")
        return

    text_raw = (message.text or "").strip()
    first_space = text_raw.find(" ")
    if first_space <= 0:
        await message.reply("Usage: /broadcast_all Your message")
        return
    header = text_raw[:first_space]
    body = text_raw[first_space + 1 :].strip()
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
async def open_admin_tools(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    if not await _is_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)
    await state.clear()
    text = (
        "<b>Admin Tools</b>\n\n"
        "Tools are grouped for easier navigation:\n"
        "- Overview & Health\n"
        "- Commerce Operations\n"
        "- Delivery Operations\n"
        "- Moderation & Risk\n"
        "- System & Access\n\n"
        "Open Commerce or Delivery to view transaction logs:\n"
        "- Successful\n"
        "- Failed\n"
        "- Delivered\n"
        "- Awaiting Delivery\n\n"
        "Danger Zone contains delete actions."
    )
    await safe_replace_with_screen(
        callback,
        text,
        parse_mode="HTML",
        reply_markup=_admin_tools_keyboard(),
    )


@router.callback_query(F.data == "admin_group_overview")
async def admin_group_overview(callback: CallbackQuery, session: AsyncSession):
    if not await _is_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)
    await safe_replace_with_screen(
        callback,
        "<b>Overview & Health</b>\n\nPlatform snapshot and flow reliability tools.",
        parse_mode="HTML",
        reply_markup=_admin_group_overview_keyboard(),
    )


@router.callback_query(F.data == "admin_group_commerce")
async def admin_group_commerce(callback: CallbackQuery, session: AsyncSession):
    if not await _is_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)
    await safe_replace_with_screen(
        callback,
        (
            "<b>Commerce Operations</b>\n\n"
            "Transactions, payouts, vendors, listings, and transaction logs.\n"
            "Use Transaction Logs for successful, failed, delivered, and awaiting-delivery views."
        ),
        parse_mode="HTML",
        reply_markup=_admin_group_commerce_keyboard(),
    )


@router.callback_query(F.data == "admin_group_delivery")
async def admin_group_delivery(callback: CallbackQuery, session: AsyncSession):
    if not await _is_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)
    await safe_replace_with_screen(
        callback,
        "<b>Delivery Operations</b>\n\nTrack jobs, assign agents, and review delivery-focused logs.",
        parse_mode="HTML",
        reply_markup=_admin_group_delivery_keyboard(),
    )


@router.callback_query(F.data == "admin_group_moderation")
async def admin_group_moderation(callback: CallbackQuery, session: AsyncSession):
    if not await _is_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)
    await safe_replace_with_screen(
        callback,
        "<b>Moderation & Risk</b>\n\nSeller approvals, complaints, and vendor privileges.",
        parse_mode="HTML",
        reply_markup=_admin_group_moderation_keyboard(),
    )


@router.callback_query(F.data == "admin_group_system")
async def admin_group_system(callback: CallbackQuery, session: AsyncSession):
    if not await _is_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)
    await safe_replace_with_screen(
        callback,
        "<b>System & Access</b>\n\nBroadcast communications, ops-admin access control, and user directory.",
        parse_mode="HTML",
        reply_markup=_admin_group_system_keyboard(),
    )


@router.callback_query(F.data == "admin_users")
async def admin_users(callback: CallbackQuery, session: AsyncSession):
    if not await _is_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)
    text = await _render_users_text(session)
    await safe_replace_with_screen(
        callback,
        text,
        parse_mode="HTML",
        reply_markup=_admin_group_system_keyboard(),
    )


@router.callback_query(F.data == "admin_transaction_logs")
async def admin_transaction_logs(callback: CallbackQuery, session: AsyncSession):
    if not await _is_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)
    await safe_replace_with_screen(
        callback,
        (
            "<b>Transaction Logs</b>\n\n"
            "Choose a quick filter:\n"
            "- Successful\n"
            "- Failed\n"
            "- Delivered\n"
            "- Awaiting Delivery"
        ),
        parse_mode="HTML",
        reply_markup=_admin_transaction_logs_keyboard(),
    )


@router.callback_query(F.data.startswith("admin_txlog_"))
async def admin_transaction_logs_filtered(callback: CallbackQuery, session: AsyncSession):
    if not await _is_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)

    filter_name = (callback.data or "").replace("admin_txlog_", "", 1).strip().lower()
    if filter_name not in {"success", "failed", "delivered", "awaiting"}:
        await safe_edit_text(callback, "Invalid log filter.", reply_markup=_admin_transaction_logs_keyboard())
        return

    text = await _render_transaction_logs_text(session, filter_name)
    await safe_replace_with_screen(
        callback,
        text,
        parse_mode="HTML",
        reply_markup=_admin_transaction_logs_keyboard(),
    )


@router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery, session: AsyncSession):
    if not await _is_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)
    text = await _render_stats_text(session)
    await safe_replace_with_screen(
        callback,
        text,
        parse_mode="HTML",
        reply_markup=_admin_group_overview_keyboard(),
    )


@router.callback_query(F.data == "admin_flow_health")
async def admin_flow_health(callback: CallbackQuery, session: AsyncSession):
    if not await _is_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)
    text = await _render_flow_health_text(session)
    await safe_replace_with_screen(
        callback,
        text,
        parse_mode="HTML",
        reply_markup=_admin_group_overview_keyboard(),
    )


@router.callback_query(F.data == "admin_vendors")
async def admin_vendors(callback: CallbackQuery, session: AsyncSession):
    if not await _is_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)
    text = await _render_vendors_text(session)
    await safe_replace_with_screen(
        callback,
        text,
        parse_mode="HTML",
        reply_markup=_admin_group_commerce_keyboard(),
    )


@router.callback_query(F.data == "admin_transactions")
async def admin_transactions(callback: CallbackQuery, session: AsyncSession):
    if not await _is_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)
    text = await _render_transactions_text(session)
    await safe_replace_with_screen(
        callback,
        text,
        parse_mode="HTML",
        reply_markup=_admin_group_commerce_keyboard(),
    )


@router.callback_query(F.data == "admin_payouts")
async def admin_payouts(callback: CallbackQuery, session: AsyncSession):
    if not await _is_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)
    text = await _render_payouts_text(session)
    await safe_replace_with_screen(
        callback,
        text,
        parse_mode="HTML",
        reply_markup=await _payouts_keyboard(session),
    )


@router.callback_query(F.data == "admin_complaints_ip")
async def admin_complaints_ip(callback: CallbackQuery, session: AsyncSession):
    if not await _is_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)
    text = await _render_complaints_ip_text(session)
    complaints = await _fetch_recent_complaints(session)
    await safe_replace_with_screen(
        callback,
        text,
        parse_mode="HTML",
        reply_markup=_complaints_dashboard_keyboard(complaints),
    )


@router.callback_query(F.data.startswith("admin_complaint_view_"))
async def admin_complaint_view(callback: CallbackQuery, session: AsyncSession):
    if not await _is_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)

    complaint_id = _callback_int_suffix(callback.data, "admin_complaint_view_")
    if complaint_id is None:
        await safe_edit_text(callback, "Invalid complaint payload.", reply_markup=_admin_tools_keyboard())
        return

    result = await session.execute(
        select(Complaint)
        .options(
            joinedload(Complaint.order).joinedload(Order.listing),
            joinedload(Complaint.complainant),
        )
        .where(Complaint.id == complaint_id)
    )
    complaint = result.scalars().first()
    if not complaint:
        await safe_edit_text(callback, "Complaint not found.", reply_markup=_admin_tools_keyboard())
        return

    listing_title = (
        complaint.order.listing.title
        if complaint.order and complaint.order.listing
        else "Unknown item"
    )
    complainant_name = (
        f"{complaint.complainant.first_name} {complaint.complainant.last_name or ''}".strip()
        if complaint.complainant
        else "Unknown"
    )
    complainant_tg = complaint.complainant.telegram_id if complaint.complainant else "N/A"
    detail_text = (
        f"<b>Complaint #{complaint.id}</b>\n\n"
        f"<b>Status:</b> {complaint.status.value}\n"
        f"<b>Order:</b> #{complaint.order_id}\n"
        f"<b>Complainant:</b> {complainant_name}\n"
        f"<b>Telegram ID:</b> <code>{complainant_tg}</code>\n"
        f"<b>Item:</b> {listing_title}\n"
        f"<b>Subject:</b> {complaint.subject}\n"
        f"<b>Description:</b>\n{complaint.description}\n\n"
        f"<b>Created:</b> {complaint.created_at.strftime('%Y-%m-%d %H:%M')}\n"
        f"<b>Evidence:</b> {'Attached' if complaint.evidence_url else 'None'}"
    )
    await safe_replace_with_screen(
        callback,
        detail_text,
        parse_mode="HTML",
        reply_markup=_complaint_detail_keyboard(complaint.id),
    )


@router.callback_query(F.data.startswith("admin_complaint_set_"))
async def admin_complaint_set_status(callback: CallbackQuery, session: AsyncSession):
    if not await _is_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)

    payload = (callback.data or "").strip()
    parts = payload.split("_")
    if len(parts) != 5 or not parts[3].isdigit():
        await safe_edit_text(callback, "Invalid complaint status payload.", reply_markup=_admin_tools_keyboard())
        return

    complaint_id = int(parts[3])
    status_code = parts[4].upper()
    status_map = {
        "I": DisputeStatus.INVESTIGATING,
        "R": DisputeStatus.RESOLVED,
        "C": DisputeStatus.CLOSED,
    }
    next_status = status_map.get(status_code)
    if not next_status:
        await safe_edit_text(callback, "Invalid complaint status.", reply_markup=_admin_tools_keyboard())
        return

    result = await session.execute(
        select(Complaint)
        .options(joinedload(Complaint.complainant))
        .where(Complaint.id == complaint_id)
    )
    complaint = result.scalars().first()
    if not complaint:
        await safe_edit_text(callback, "Complaint not found.", reply_markup=_admin_tools_keyboard())
        return

    complaint.status = next_status
    complaint.resolved_at = datetime.now(timezone.utc) if next_status in {DisputeStatus.RESOLVED, DisputeStatus.CLOSED} else None
    await session.commit()

    if complaint.complainant and complaint.complainant.telegram_id and complaint.complainant.telegram_id.isdigit():
        try:
            await callback.bot.send_message(
                chat_id=int(complaint.complainant.telegram_id),
                text=(
                    f"Update on your complaint #{complaint.id} for order #{complaint.order_id}:\n"
                    f"Status is now {complaint.status.value}."
                ),
            )
        except Exception:
            pass

    text = await _render_complaints_ip_text(session)
    complaints = await _fetch_recent_complaints(session)
    await safe_replace_with_screen(
        callback,
        f"{text}\n\n<b>Updated:</b> Complaint #{complaint.id} -> {complaint.status.value}",
        parse_mode="HTML",
        reply_markup=_complaints_dashboard_keyboard(complaints),
    )


@router.callback_query(F.data.startswith("admin_retry_payout_"))
async def admin_retry_failed_payout(callback: CallbackQuery, session: AsyncSession):
    if not await _is_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)

    order_id = _callback_int_suffix(callback.data, "admin_retry_payout_")
    if order_id is None:
        await safe_edit_text(callback, "Invalid payout payload.", reply_markup=await _payouts_keyboard(session))
        return

    escrow = get_escrow_service()
    ok, status = await escrow.retry_failed_payout(order_id, session)
    if not ok:
        await safe_edit_text(
            callback,
            (
                "<b>Payout Retry</b>\n\n"
                f"Order #{order_id}\n"
                f"Result: {status}"
            ),
            parse_mode="HTML",
            reply_markup=await _payouts_keyboard(session),
        )
        return

    text = await _render_payouts_text(session)
    await safe_edit_text(
        callback,
        f"{text}\n\n<b>Retry queued</b> for order #{order_id} (status: {status}).",
        parse_mode="HTML",
        reply_markup=await _payouts_keyboard(session),
    )


@router.callback_query(F.data == "admin_danger_tools")
async def admin_danger_tools(callback: CallbackQuery, session: AsyncSession):
    if not await _is_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)
    await safe_replace_with_screen(
        callback,
        (
            "<b>Danger Zone</b>\n\n"
            "Use these actions carefully.\n"
            "Delete actions are irreversible."
        ),
        parse_mode="HTML",
        reply_markup=_danger_tools_keyboard(),
    )


@router.callback_query(F.data == "admin_wipe_marketplace_confirm")
async def admin_wipe_marketplace_confirm(callback: CallbackQuery, session: AsyncSession):
    if not await _is_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)
    await safe_replace_with_screen(
        callback,
        (
            "<b>Confirm Data Wipe</b>\n\n"
            "This will permanently clear:\n"
            "- Listings\n"
            "- Cart items\n"
            "- Orders and complaints\n"
            "- Deliveries, pickup records, and delivery events\n"
            "- Webhook receipts\n\n"
            "This cannot be undone."
        ),
        parse_mode="HTML",
        reply_markup=_wipe_marketplace_confirm_keyboard(),
    )


@router.callback_query(F.data == "admin_wipe_marketplace_execute")
async def admin_wipe_marketplace_execute(callback: CallbackQuery, session: AsyncSession):
    if not await _is_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)
    try:
        counts = await _wipe_transactions_and_listings(session)
        await session.commit()
    except Exception:
        await session.rollback()
        await safe_replace_with_screen(
            callback,
            "Could not clear data right now. Please retry.",
            reply_markup=_wipe_marketplace_done_keyboard(),
        )
        return

    await safe_replace_with_screen(
        callback,
        (
            "<b>Data Cleared</b>\n\n"
            f"Listings: {counts.get('listings', 0)}\n"
            f"Cart Items: {counts.get('cart_items', 0)}\n"
            f"Orders: {counts.get('orders', 0)}\n"
            f"Complaints: {counts.get('complaints', 0)}\n"
            f"Deliveries: {counts.get('deliveries', 0)}\n"
            f"Delivery Orders: {counts.get('delivery_orders', 0)}\n"
            f"Delivery Events: {counts.get('delivery_events', 0)}\n"
            f"Webhook Receipts: {counts.get('webhook_receipts', 0)}"
        ),
        parse_mode="HTML",
        reply_markup=_wipe_marketplace_done_keyboard(),
    )


@router.callback_query(F.data == "admin_ops_admins")
async def admin_ops_admins(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    if not await _is_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    if not await _is_super_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Only super admin can manage ops admins.", show_alert=True)
        return
    await safe_answer_callback(callback)
    await state.clear()
    result = await session.execute(select(AdminUser).order_by(AdminUser.created_at.desc()).limit(50))
    admins = result.scalars().all()
    text = await _render_ops_admins_text(session)
    await safe_replace_with_screen(
        callback,
        text,
        parse_mode="HTML",
        reply_markup=_ops_admins_keyboard(admins),
    )


@router.callback_query(F.data == "admin_ops_add")
async def admin_ops_add(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    if not await _is_super_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Only super admin can add ops admins.", show_alert=True)
        return
    await safe_answer_callback(callback)
    await state.set_state(AdminStates.awaiting_ops_admin_telegram_id)
    await safe_replace_with_screen(
        callback,
        "Send Telegram ID to grant ops admin access.",
        reply_markup=_delete_help_keyboard(),
    )


@router.message(AdminStates.awaiting_ops_admin_telegram_id)
async def admin_ops_add_receive_id(message: Message, session: AsyncSession, state: FSMContext):
    if not await _is_super_admin(message.from_user.id, session):
        await state.clear()
        await message.reply("Only super admin can add ops admins.")
        return

    telegram_id = (message.text or "").strip()
    if not telegram_id.isdigit():
        await message.reply("Send a valid numeric Telegram ID.")
        return
    if str(telegram_id) == str(settings.admin_telegram_id):
        await message.reply("This ID is already super admin.")
        return

    result = await session.execute(select(AdminUser).where(AdminUser.telegram_id == telegram_id))
    existing = result.scalars().first()
    if existing:
        existing.role = AdminRole.OPS_ADMIN
    else:
        session.add(AdminUser(telegram_id=telegram_id, role=AdminRole.OPS_ADMIN))
    await session.commit()
    await state.clear()
    await message.reply(f"Ops admin added: {telegram_id}")


@router.callback_query(F.data.startswith("admin_ops_remove_"))
async def admin_ops_remove(callback: CallbackQuery, session: AsyncSession):
    if not await _is_super_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Only super admin can remove ops admins.", show_alert=True)
        return
    await safe_answer_callback(callback)
    admin_id = _callback_int_suffix(callback.data, "admin_ops_remove_")
    if admin_id is None:
        await safe_edit_text(callback, "Invalid ops admin payload.", reply_markup=_admin_tools_keyboard())
        return
    admin_user = await session.get(AdminUser, admin_id)
    if not admin_user:
        await safe_edit_text(callback, "Ops admin not found.", reply_markup=_admin_tools_keyboard())
        return
    await session.delete(admin_user)
    await session.commit()
    result = await session.execute(select(AdminUser).order_by(AdminUser.created_at.desc()).limit(50))
    admins = result.scalars().all()
    text = await _render_ops_admins_text(session)
    await safe_replace_with_screen(
        callback,
        text,
        parse_mode="HTML",
        reply_markup=_ops_admins_keyboard(admins),
    )


@router.callback_query(F.data == "admin_listings")
async def admin_listings(callback: CallbackQuery, session: AsyncSession):
    if not await _is_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)
    text = await _render_listings_text(session)
    await safe_replace_with_screen(
        callback,
        text,
        parse_mode="HTML",
        reply_markup=_admin_group_commerce_keyboard(),
    )


@router.callback_query(F.data == "admin_delivery_agents")
async def admin_delivery_agents(callback: CallbackQuery, session: AsyncSession):
    if not await _is_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)
    result = await session.execute(
        select(DeliveryAgent)
        .order_by(DeliveryAgent.created_at.desc())
        .limit(20)
    )
    agents = result.scalars().all()
    text = await _render_delivery_agents_text(session)
    await safe_replace_with_screen(
        callback,
        text,
        parse_mode="HTML",
        reply_markup=_delivery_agents_keyboard(agents),
    )


@router.callback_query(F.data.startswith("admin_delivery_agent_activate_"))
async def admin_delivery_agent_activate(callback: CallbackQuery, session: AsyncSession):
    if not await _is_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)
    agent_id = _callback_int_suffix(callback.data, "admin_delivery_agent_activate_")
    if agent_id is None:
        await safe_edit_text(callback, "Invalid agent payload.", reply_markup=_admin_tools_keyboard())
        return
    agent = await session.get(DeliveryAgent, agent_id)
    if not agent:
        await safe_edit_text(callback, "Agent not found.", reply_markup=_admin_tools_keyboard())
        return
    agent.is_active = True
    await session.commit()
    await admin_delivery_agents(callback, session)


@router.callback_query(F.data.startswith("admin_delivery_agent_deactivate_"))
async def admin_delivery_agent_deactivate(callback: CallbackQuery, session: AsyncSession):
    if not await _is_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)
    agent_id = _callback_int_suffix(callback.data, "admin_delivery_agent_deactivate_")
    if agent_id is None:
        await safe_edit_text(callback, "Invalid agent payload.", reply_markup=_admin_tools_keyboard())
        return
    agent = await session.get(DeliveryAgent, agent_id)
    if not agent:
        await safe_edit_text(callback, "Agent not found.", reply_markup=_admin_tools_keyboard())
        return
    agent.is_active = False
    await session.commit()
    await admin_delivery_agents(callback, session)


@router.callback_query(F.data == "admin_delivery_agent_add")
async def admin_delivery_agent_add(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    if not await _is_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)
    await state.clear()
    await state.set_state(AdminStates.awaiting_delivery_agent_name)
    await safe_replace_with_screen(
        callback,
        "<b>Add Delivery Agent</b>\n\nStep 1/4\nSend the agent full name.",
        parse_mode="HTML",
        reply_markup=_delete_help_keyboard(),
    )


@router.message(AdminStates.awaiting_delivery_agent_name)
async def receive_delivery_agent_name(message: Message, state: FSMContext, session: AsyncSession):
    if not await _is_admin(message.from_user.id, session):
        await message.reply("You are not authorized to use this command.")
        await state.clear()
        return

    name = (message.text or "").strip()
    if len(name) < 2:
        await message.reply("Please send a valid name (at least 2 characters).")
        return

    await state.update_data(delivery_agent_name=name)
    await state.set_state(AdminStates.awaiting_delivery_agent_phone)
    await message.reply("Step 2/4\nSend phone number (or type 'none').")


@router.message(AdminStates.awaiting_delivery_agent_phone)
async def receive_delivery_agent_phone(message: Message, state: FSMContext, session: AsyncSession):
    if not await _is_admin(message.from_user.id, session):
        await message.reply("You are not authorized to use this command.")
        await state.clear()
        return

    phone = (message.text or "").strip()
    if phone.lower() == "none":
        phone = ""

    await state.update_data(delivery_agent_phone=phone)
    await state.set_state(AdminStates.awaiting_delivery_agent_vehicle)
    await message.reply("Step 3/4\nSend vehicle type (e.g., Bike) or type 'none'.")


@router.message(AdminStates.awaiting_delivery_agent_vehicle)
async def receive_delivery_agent_vehicle(message: Message, state: FSMContext, session: AsyncSession):
    if not await _is_admin(message.from_user.id, session):
        await message.reply("You are not authorized to use this command.")
        await state.clear()
        return

    vehicle = (message.text or "").strip()
    if vehicle.lower() == "none":
        vehicle = ""

    data = await state.get_data()
    name = (data.get("delivery_agent_name") or "").strip()
    if not name:
        await state.clear()
        await message.reply("Session expired. Open /admin_tools and try again.")
        return

    await state.update_data(delivery_agent_vehicle=vehicle)
    await state.set_state(AdminStates.awaiting_delivery_agent_telegram_id)
    await message.reply("Step 4/4\nSend the agent's Telegram user ID (numeric, e.g., 123456789).")


@router.message(AdminStates.awaiting_delivery_agent_telegram_id)
async def receive_delivery_agent_telegram_id(message: Message, state: FSMContext, session: AsyncSession):
    if not await _is_admin(message.from_user.id, session):
        await message.reply("You are not authorized to use this command.")
        await state.clear()
        return

    telegram_id = (message.text or "").strip()
    if not telegram_id.isdigit():
        await message.reply("Please send a valid Telegram user ID (numeric only).")
        return

    data = await state.get_data()
    name = (data.get("delivery_agent_name") or "").strip()
    phone = (data.get("delivery_agent_phone") or "").strip()
    vehicle = (data.get("delivery_agent_vehicle") or "").strip()

    if not name:
        await state.clear()
        await message.reply("Session expired. Open /admin_tools and try again.")
        return

    existing_result = await session.execute(
        select(DeliveryAgent).where(DeliveryAgent.telegram_id == telegram_id)
    )
    existing_agent = existing_result.scalars().first()

    if existing_agent:
        existing_agent.name = name
        existing_agent.phone = phone or None
        existing_agent.vehicle_type = vehicle or None
        existing_agent.is_active = True
        try:
            await session.commit()
            await session.refresh(existing_agent)
        except IntegrityError:
            await session.rollback()
            await message.reply(
                "Could not update this agent right now due to a database conflict. Please retry."
            )
            return
        await state.clear()
        await message.reply(
            (
                "<b>Delivery Agent Updated</b>\n\n"
                f"<b>ID:</b> {existing_agent.id}\n"
                f"<b>Name:</b> {existing_agent.name}\n"
                f"<b>Phone:</b> {existing_agent.phone or 'N/A'}\n"
                f"<b>Vehicle:</b> {existing_agent.vehicle_type or 'N/A'}\n"
                f"<b>Telegram ID:</b> <code>{telegram_id}</code>\n\n"
                "This Telegram ID already existed, so the agent profile was updated."
            ),
            parse_mode="HTML",
            reply_markup=_admin_tools_keyboard(),
        )
        return

    agent = DeliveryAgent(
        name=name,
        phone=phone or None,
        vehicle_type=vehicle or None,
        telegram_id=telegram_id,
        is_active=True,
    )
    session.add(agent)
    try:
        await session.commit()
        await session.refresh(agent)
    except IntegrityError:
        await session.rollback()
        await message.reply(
            "An agent with this Telegram ID already exists. Open Admin Tools and update the existing agent."
        )
        return
    await state.clear()

    await message.reply(
        (
            "<b>Delivery Agent Added</b>\n\n"
            f"<b>ID:</b> {agent.id}\n"
            f"<b>Name:</b> {agent.name}\n"
            f"<b>Phone:</b> {agent.phone or 'N/A'}\n"
            f"<b>Vehicle:</b> {agent.vehicle_type or 'N/A'}\n"
            f"<b>Telegram ID:</b> <code>{telegram_id}</code>\n\n"
            "Agent registered via Telegram authentication."
        ),
        parse_mode="HTML",
        reply_markup=_admin_tools_keyboard(),
    )


@router.callback_query(F.data == "admin_delivery_tracking")
async def admin_delivery_tracking(callback: CallbackQuery, session: AsyncSession):
    if not await _is_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)
    text = await _render_delivery_tracking_text(session)
    await safe_replace_with_screen(
        callback,
        text,
        parse_mode="HTML",
        reply_markup=_admin_group_delivery_keyboard(),
    )


@router.callback_query(F.data == "admin_delivery_assign_picker")
async def admin_delivery_assign_picker(callback: CallbackQuery, session: AsyncSession):
    if not await _is_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)
    result = await session.execute(
        select(Delivery)
        .where(Delivery.status == DeliveryStatus.PENDING_ASSIGNMENT)
        .order_by(Delivery.updated_at.desc())
        .limit(15)
    )
    deliveries = result.scalars().all()
    if not deliveries:
        await safe_replace_with_screen(
            callback,
            "<b>Assignment Queue</b>\n\nNo deliveries available for assignment.",
            parse_mode="HTML",
            reply_markup=_admin_group_delivery_keyboard(),
        )
        return
    await safe_replace_with_screen(
        callback,
        "<b>Assignment Queue</b>\n\nSelect a pending delivery job to assign.",
        parse_mode="HTML",
        reply_markup=_delivery_assign_pick_keyboard(deliveries),
    )


@router.callback_query(F.data.startswith("admin_delivery_assign_"))
async def admin_delivery_assign_select(callback: CallbackQuery, session: AsyncSession):
    if not await _is_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)

    delivery_id = _callback_int_suffix(callback.data, "admin_delivery_assign_")
    if delivery_id is None:
        await safe_edit_text(callback, "Invalid delivery selection.", reply_markup=_admin_tools_keyboard())
        return
    delivery = await session.get(Delivery, delivery_id)
    if not delivery:
        await safe_edit_text(callback, "Delivery not found.", reply_markup=_admin_tools_keyboard())
        return

    result = await session.execute(
        select(DeliveryAgent)
        .where(DeliveryAgent.is_active.is_(True))
        .order_by(DeliveryAgent.created_at.desc())
        .limit(20)
    )
    agents = result.scalars().all()
    if not agents:
        await safe_edit_text(
            callback,
            "<b>No Active Agents</b>\n\nCreate/activate delivery agents first.",
            parse_mode="HTML",
            reply_markup=_admin_tools_keyboard(),
        )
        return

    await safe_edit_text(
        callback,
        f"<b>Assign Agent</b>\n\nDelivery #{delivery.id}",
        parse_mode="HTML",
        reply_markup=_delivery_assign_agent_keyboard(delivery.id, agents),
    )


@router.callback_query(F.data.startswith("admin_delivery_set_"))
async def admin_delivery_set_agent(callback: CallbackQuery, session: AsyncSession):
    if not await _is_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)

    match = re.match(r"^admin_delivery_set_(\d+)_(\d+)$", callback.data or "")
    if not match:
        await safe_edit_text(callback, "Invalid delivery assignment payload.", reply_markup=_admin_tools_keyboard())
        return
    delivery_id = int(match.group(1))
    agent_id = int(match.group(2))

    result = await session.execute(
        select(Delivery)
        .options(
            joinedload(Delivery.order).joinedload(Order.buyer),
            joinedload(Delivery.delivery_orders).joinedload(DeliveryOrder.order).joinedload(Order.buyer),
            joinedload(Delivery.delivery_orders).joinedload(DeliveryOrder.order).joinedload(Order.listing),
        )
        .where(Delivery.id == delivery_id)
    )
    delivery = result.unique().scalars().first()
    if not delivery:
        await safe_edit_text(callback, "Delivery not found.", reply_markup=_admin_tools_keyboard())
        return

    agent = await session.get(DeliveryAgent, agent_id)
    if not agent or not agent.is_active:
        await safe_edit_text(callback, "Agent not available.", reply_markup=_admin_tools_keyboard())
        return

    # Idempotency guard: avoid duplicate notifications when assignment is repeated.
    if delivery.agent_id == agent.id and delivery.status == DeliveryStatus.ASSIGNED:
        await safe_edit_text(
            callback,
            (
                "<b>Already Assigned</b>\n\n"
                f"Delivery #{delivery.id} is already assigned to {agent.name} (#{agent.id})."
            ),
            parse_mode="HTML",
            reply_markup=_admin_tools_keyboard(),
        )
        return

    delivery.agent_id = agent.id
    delivery.status = DeliveryStatus.ASSIGNED
    if delivery.order_id:
        await _ensure_delivery_order_link(session, delivery.id, delivery.order_id)
    await session.commit()

    linked_orders: list[Order] = []
    for delivery_order in sorted(delivery.delivery_orders or [], key=lambda item: (item.sequence, item.id)):
        if delivery_order.order:
            linked_orders.append(delivery_order.order)
    if not linked_orders and delivery.order:
        linked_orders = [delivery.order]

    buyer = linked_orders[0].buyer if linked_orders else None
    if buyer and buyer.telegram_id:
        try:
            first_order_id = linked_orders[0].id
            order_ids_text = ", ".join(f"#{order.id}" for order in linked_orders)
            track_keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="Track Delivery",
                            callback_data=f"order_track_{first_order_id}",
                        )
                    ]
                ]
            )
            await callback.bot.send_message(
                chat_id=int(buyer.telegram_id),
                text=(
                    "Your delivery has been assigned to an agent.\n\n"
                    f"Order(s): {order_ids_text}\n"
                    f"Agent: {agent.name}\n"
                    f"Phone: {agent.phone or 'N/A'}\n\n"
                    "Tap Track Delivery for live status."
                ),
                reply_markup=track_keyboard,
            )
        except Exception:
            logger.exception("Failed to notify buyer for delivery assignment delivery_id=%s", delivery.id)

    # Notify agent via Telegram with pickup details
    if agent.telegram_id:
        from services.delivery_notifications import notify_agent_delivery_assigned
        try:
            await notify_agent_delivery_assigned(delivery.id, session)
        except Exception:
            logger.exception("Failed to notify assigned agent delivery_id=%s agent_id=%s", delivery.id, agent.id)
            try:
                fallback_keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="START PICKUP",
                                callback_data=f"delivery_start_pickup_{delivery.id}",
                            )
                        ]
                    ]
                )
                await callback.bot.send_message(
                    chat_id=int(agent.telegram_id),
                    text=(
                        "<b>New Delivery Job</b>\n\n"
                        f"<b>Delivery ID:</b> {delivery.id}\n"
                        f"<b>Order ID:</b> {delivery.order_id}\n"
                        "Tap START PICKUP to begin."
                    ),
                    parse_mode="HTML",
                    reply_markup=fallback_keyboard,
                )
            except Exception:
                logger.exception("Failed fallback agent notification delivery_id=%s agent_id=%s", delivery.id, agent.id)
    else:
        if callback.message:
            await callback.message.answer(
                (
                    "Assigned, but this agent has no Telegram ID on profile.\n"
                    "Add Telegram ID to the agent to receive agent action buttons."
                )
            )

    await safe_edit_text(
        callback,
        (
            "<b>Delivery Assigned</b>\n\n"
            f"Order #{delivery.order_id}\n"
            f"Delivery #{delivery.id}\n"
            f"Agent: {agent.name} (#{agent.id})"
        ),
        parse_mode="HTML",
        reply_markup=_admin_tools_keyboard(),
    )


@router.callback_query(F.data.startswith("admin_delete_listing_"))
async def delete_listing_by_button(callback: CallbackQuery, session: AsyncSession):
    if not await _is_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)

    listing_id = _callback_int_suffix(callback.data, "admin_delete_listing_")
    if listing_id is None:
        await safe_edit_text(callback, "Invalid listing payload.", reply_markup=_delete_help_keyboard())
        return
    listing = await session.get(Listing, listing_id)
    if not listing:
        await safe_edit_text(callback, "Listing not found.", reply_markup=_delete_help_keyboard())
        return

    order_count_result = await session.execute(
        select(func.count(Order.id)).where(Order.listing_id == listing_id)
    )
    if (order_count_result.scalar() or 0) > 0:
        await safe_edit_text(
            callback,
            "Cannot delete listing with existing transactions.",
            reply_markup=_delete_help_keyboard(),
        )
        return

    await session.delete(listing)
    await session.commit()
    await safe_edit_text(
        callback,
        f"Listing {listing.listing_code} deleted.",
        reply_markup=_delete_help_keyboard(),
    )


@router.callback_query(F.data.startswith("admin_delete_vendor_"))
async def delete_vendor_by_button(callback: CallbackQuery, session: AsyncSession):
    if not await _is_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)

    seller_id = _callback_int_suffix(callback.data, "admin_delete_vendor_")
    if seller_id is None:
        await safe_edit_text(callback, "Invalid vendor payload.", reply_markup=_delete_help_keyboard())
        return
    seller = await session.get(SellerProfile, seller_id)
    if not seller:
        await safe_edit_text(callback, "Vendor not found.", reply_markup=_delete_help_keyboard())
        return

    order_count_result = await session.execute(
        select(func.count(Order.id)).where(Order.seller_id == seller_id)
    )
    if (order_count_result.scalar() or 0) > 0:
        await safe_edit_text(
            callback,
            "Cannot delete vendor with existing transactions.",
            reply_markup=_delete_help_keyboard(),
        )
        return

    await session.delete(seller)
    await session.commit()
    await safe_edit_text(
        callback,
        f"Vendor {seller.seller_code} deleted.",
        reply_markup=_delete_help_keyboard(),
    )


@router.callback_query(F.data.startswith("admin_delete_user_"))
async def delete_user_by_button(callback: CallbackQuery, session: AsyncSession):
    if not await _is_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)

    user_id = _callback_int_suffix(callback.data, "admin_delete_user_")
    if user_id is None:
        await safe_edit_text(callback, "Invalid user payload.", reply_markup=_delete_help_keyboard())
        return
    user = await session.get(User, user_id)
    if not user:
        await safe_edit_text(callback, "User not found.", reply_markup=_delete_help_keyboard())
        return

    buyer_order_count_result = await session.execute(
        select(func.count(Order.id)).where(Order.buyer_id == user_id)
    )
    if (buyer_order_count_result.scalar() or 0) > 0:
        await safe_edit_text(
            callback,
            "Cannot delete user with buyer transactions.",
            reply_markup=_delete_help_keyboard(),
        )
        return

    seller_result = await session.execute(select(SellerProfile.id).where(SellerProfile.user_id == user_id))
    seller_id = seller_result.scalar_one_or_none()
    if seller_id is not None:
        seller_order_count_result = await session.execute(
            select(func.count(Order.id)).where(Order.seller_id == seller_id)
        )
        if (seller_order_count_result.scalar() or 0) > 0:
            await safe_edit_text(
                callback,
                "Cannot delete user with seller transactions.",
                reply_markup=_delete_help_keyboard(),
            )
            return

    await session.delete(user)
    await session.commit()
    await safe_edit_text(
        callback,
        f"User {user_id} deleted.",
        reply_markup=_delete_help_keyboard(),
    )


@router.callback_query(F.data.startswith("admin_delete_help_"))
async def admin_delete_help(callback: CallbackQuery, session: AsyncSession):
    if not await _is_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)

    target = callback.data.replace("admin_delete_help_", "")
    if target == "listing":
        result = await session.execute(
            select(Listing).order_by(Listing.created_at.desc()).limit(10)
        )
        listings = result.scalars().all()
        buttons = [
            (
                f"Delete {listing.listing_code}: {listing.title[:24]}",
                f"admin_delete_listing_{listing.id}",
            )
            for listing in listings
        ]
        text = (
            "<b>Delete Listing</b>\n\n"
            "Tap a listing below to delete it."
            if listings
            else "<b>Delete Listing</b>\n\nNo listings found."
        )
        keyboard = _delete_picker_keyboard(buttons)
    elif target == "vendor":
        result = await session.execute(
            select(SellerProfile).options(joinedload(SellerProfile.user)).order_by(SellerProfile.created_at.desc()).limit(10)
        )
        sellers = result.scalars().all()
        buttons = [
            (
                f"Delete {seller.seller_code}: {(seller.user.first_name if seller.user else 'Unknown')[:20]}",
                f"admin_delete_vendor_{seller.id}",
            )
            for seller in sellers
        ]
        text = (
            "<b>Delete Vendor</b>\n\n"
            "Tap a vendor below to delete."
            if sellers
            else "<b>Delete Vendor</b>\n\nNo vendors found."
        )
        keyboard = _delete_picker_keyboard(buttons)
    else:
        result = await session.execute(select(User).order_by(User.created_at.desc()).limit(10))
        users = result.scalars().all()
        buttons = [
            (
                f"Delete user #{user.id}: {user.first_name[:20]}",
                f"admin_delete_user_{user.id}",
            )
            for user in users
        ]
        text = (
            "<b>Delete User</b>\n\n"
            "Tap a user below to delete."
            if users
            else "<b>Delete User</b>\n\nNo users found."
        )
        keyboard = _delete_picker_keyboard(buttons)

    await safe_edit_text(callback, text, parse_mode="HTML", reply_markup=keyboard)


@router.callback_query(F.data == "admin_privileges_help")
async def admin_privileges_help(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    if not await _is_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)
    await state.clear()
    await state.set_state(AdminStates.awaiting_privilege_seller_id)
    text = (
        "<b>Vendor Privileges</b>\n\n"
        "Step 1/3\n"
        "Send the <b>Seller ID</b> (e.g. SEL-ABC12345) you want to update."
    )
    await safe_replace_with_screen(
        callback,
        text,
        parse_mode="HTML",
        reply_markup=_delete_help_keyboard(),
    )


@router.message(AdminStates.awaiting_privilege_seller_id)
async def receive_privilege_seller_id(message: Message, state: FSMContext, session: AsyncSession):
    if not await _is_admin(message.from_user.id, session):
        await message.reply("You are not authorized to use this command.")
        await state.clear()
        return

    seller_identifier = (message.text or "").strip()
    seller = await _find_seller_by_identifier(session, seller_identifier)
    if not seller:
        await message.reply("Vendor not found. Send seller code (SEL-...) or numeric ID.")
        return

    await state.update_data(seller_id=seller.id)
    await state.set_state(AdminStates.awaiting_privilege_featured)
    await message.reply(
        f"Step 2/3\nSeller {seller.seller_code} found.\nChoose featured status:",
        reply_markup=_privilege_featured_keyboard(),
    )


@router.callback_query(
    F.data.startswith("admin_priv_featured_"),
    StateFilter(AdminStates.awaiting_privilege_featured),
)
async def receive_privilege_featured(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    if not await _is_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        await state.clear()
        return

    await safe_answer_callback(callback)
    is_featured = callback.data.endswith("_1")
    await state.update_data(is_featured=is_featured)
    await state.set_state(AdminStates.awaiting_privilege_priority)
    await safe_edit_text(
        callback,
        "Step 3/3\nSend a priority score from 0 to 100.",
        reply_markup=_delete_help_keyboard(),
    )


@router.message(AdminStates.awaiting_privilege_priority)
async def receive_privilege_priority(message: Message, state: FSMContext, session: AsyncSession):
    if not await _is_admin(message.from_user.id, session):
        await message.reply("You are not authorized to use this command.")
        await state.clear()
        return

    priority_text = (message.text or "").strip()
    if not priority_text.isdigit():
        await message.reply("Priority must be a number between 0 and 100.")
        return

    priority_score = int(priority_text)
    if priority_score < 0 or priority_score > 100:
        await message.reply("Priority must be between 0 and 100.")
        return

    data = await state.get_data()
    seller_id = data.get("seller_id")
    is_featured = data.get("is_featured")
    if seller_id is None or is_featured is None:
        await state.clear()
        await message.reply("Session expired. Open /admin_tools and try again.")
        return

    seller = await session.get(SellerProfile, int(seller_id))
    if not seller:
        await state.clear()
        await message.reply("Vendor not found. Open /admin_tools and try again.")
        return

    seller.is_featured = bool(is_featured)
    seller.priority_score = priority_score
    await session.commit()
    await state.clear()
    await message.reply(
        f"Vendor {seller.seller_code} updated.\nFeatured: {'Yes' if seller.is_featured else 'No'}\n"
        f"Priority: {seller.priority_score}",
        reply_markup=_admin_tools_keyboard(),
    )


@router.callback_query(F.data == "admin_broadcast_help")
async def admin_broadcast_help(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    if not await _is_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)
    await state.clear()
    text = (
        "<b>Broadcast</b>\n\n"
        "Choose an audience to continue."
    )
    await safe_replace_with_screen(
        callback,
        text,
        parse_mode="HTML",
        reply_markup=_broadcast_audience_keyboard(),
    )


@router.callback_query(F.data.startswith("admin_broadcast_audience_"))
async def choose_broadcast_audience(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    if not await _is_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return

    await safe_answer_callback(callback)
    audience = callback.data.replace("admin_broadcast_audience_", "")
    await state.clear()
    await state.update_data(broadcast_audience=audience)
    await state.set_state(AdminStates.awaiting_broadcast_message)
    await safe_replace_with_screen(
        callback,
        (
            f"Audience selected: <b>{audience}</b>\n\n"
            "Now send text or send a photo (caption optional).\n"
            "You can personalize with <code>{{first_name}}</code>."
        ),
        parse_mode="HTML",
        reply_markup=_delete_help_keyboard(),
    )


@router.message(AdminStates.awaiting_broadcast_message, F.photo)
async def send_photo_broadcast_from_state(message: Message, state: FSMContext, session: AsyncSession):
    if not await _is_admin(message.from_user.id, session):
        await message.reply("You are not authorized to use this command.")
        await state.clear()
        return

    if not message.photo:
        await message.reply("Please send a valid image.")
        return

    data = await state.get_data()
    audience = data.get("broadcast_audience")
    if not audience:
        await state.clear()
        await message.reply("Session expired. Open /admin_tools and try again.")
        return

    photo_file_id = message.photo[-1].file_id
    caption = (message.caption or "").strip() or None

    recipients = await _get_broadcast_recipients(session, str(audience))
    sent, failed = await _send_broadcast(
        message.bot,
        recipients,
        photo_file_id=photo_file_id,
        caption=caption,
    )
    await state.clear()
    await message.reply(
        f"Photo broadcast complete.\nAudience: {audience}\nRecipients: {len(recipients)}\n"
        f"Sent: {sent}\nFailed: {failed}",
        reply_markup=_admin_tools_keyboard(),
    )


@router.message(AdminStates.awaiting_broadcast_message)
async def send_broadcast_from_state(message: Message, state: FSMContext, session: AsyncSession):
    if not await _is_admin(message.from_user.id, session):
        await message.reply("You are not authorized to use this command.")
        await state.clear()
        return

    text = (message.text or "").strip()
    if not text:
        await message.reply("Broadcast message cannot be empty. Send text or send a photo.")
        return

    data = await state.get_data()
    audience = data.get("broadcast_audience")
    if not audience:
        await state.clear()
        await message.reply("Session expired. Open /admin_tools and try again.")
        return

    recipients = await _get_broadcast_recipients(session, str(audience))
    sent, failed = await _send_broadcast(message.bot, recipients, message_text=text)
    await state.clear()
    await message.reply(
        f"Broadcast complete.\nAudience: {audience}\nRecipients: {len(recipients)}\n"
        f"Sent: {sent}\nFailed: {failed}",
        reply_markup=_admin_tools_keyboard(),
    )


@router.callback_query(F.data == "admin_pending_refresh")
async def refresh_pending(callback: CallbackQuery, session: AsyncSession):
    if not await _is_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)
    text = await _render_pending_text(session)
    await safe_replace_with_screen(
        callback,
        text,
        parse_mode="HTML",
        reply_markup=_pending_keyboard(),
    )


@router.callback_query(F.data.startswith("admin_approve_"))
async def approve_seller(callback: CallbackQuery, session: AsyncSession):
    if not await _is_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)

    seller_id = _callback_int_suffix(callback.data, "admin_approve_")
    if seller_id is None:
        await safe_edit_text(callback, "Invalid seller payload.", reply_markup=_pending_keyboard())
        return
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
        f"Seller {seller.seller_code} approved successfully.",
        reply_markup=_pending_keyboard(),
    )


@router.callback_query(F.data.startswith("admin_reject_"))
async def reject_seller(callback: CallbackQuery, session: AsyncSession):
    if not await _is_admin(callback.from_user.id, session):
        await safe_answer_callback(callback, text="Not authorized", show_alert=True)
        return
    await safe_answer_callback(callback)

    seller_id = _callback_int_suffix(callback.data, "admin_reject_")
    if seller_id is None:
        await safe_edit_text(callback, "Invalid seller payload.", reply_markup=_pending_keyboard())
        return
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
        f"Seller {seller.seller_code} marked as not approved.",
        reply_markup=_pending_keyboard(),
    )
