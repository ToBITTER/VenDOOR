"""
Buyer orders handler - view orders, confirm receipt, raise disputes.
"""

import re
from datetime import datetime

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.helpers.brand_assets import get_empty_state
from bot.helpers.telegram import safe_answer_callback, safe_replace_with_screen
from bot.keyboards.main_menu import get_main_menu_inline
from core.config import get_settings
from db.models import Delivery, DeliveryOrder, DeliveryStatus, Order, OrderStatus, SellerProfile, User
from services.escrow import get_escrow_service
from services.korapay import get_korapay_client

settings = get_settings()

router = Router()


def _callback_int_suffix(callback_data: str | None, prefix: str) -> int | None:
    payload = (callback_data or "").strip()
    if not payload.startswith(prefix):
        return None
    value = payload.replace(prefix, "", 1).strip()
    if not value.isdigit():
        return None
    return int(value)


def _cart_group_ref(order: Order) -> str | None:
    ref = str(order.transaction_ref or "").strip()
    if not ref.startswith("VENDOOR_CART_"):
        return None
    # Orders created from cart payment share this root reference.
    # Additional orders may be stored as "<root>_<order_id>".
    if re.search(r"_\d+$", ref):
        return re.sub(r"_\d+$", "", ref)
    return ref


async def _effective_delivery(order: Order, session: AsyncSession) -> Delivery | None:
    """
    Resolve shared delivery job for an order.
    Falls back to DeliveryOrder link for grouped cart deliveries.
    """
    # Avoid relationship lazy-loading in async handlers (can trigger MissingGreenlet).
    direct_result = await session.execute(
        select(Delivery)
        .options(selectinload(Delivery.agent))
        .where(Delivery.order_id == order.id)
        .limit(1)
    )
    direct_delivery = direct_result.scalars().first()
    if direct_delivery:
        return direct_delivery

    result = await session.execute(
        select(Delivery)
        .options(selectinload(Delivery.agent))
        .join(DeliveryOrder, DeliveryOrder.delivery_id == Delivery.id)
        .where(DeliveryOrder.order_id == order.id)
        .order_by(Delivery.created_at.desc())
        .limit(1)
    )
    return result.scalars().first()


async def _group_orders_by_delivery(orders: list[Order], session: AsyncSession) -> list[dict]:
    groups: dict[str, dict] = {}
    for order in orders:
        delivery = await _effective_delivery(order, session)
        if delivery:
            key = f"delivery:{delivery.id}"
            group = groups.setdefault(
                key,
                {"kind": "delivery", "delivery": delivery, "orders": [], "latest": order.created_at},
            )
        else:
            cart_ref = _cart_group_ref(order)
            if cart_ref:
                key = f"cart:{cart_ref}"
                group = groups.setdefault(
                    key,
                    {
                        "kind": "cart",
                        "delivery": None,
                        "orders": [],
                        "latest": order.created_at,
                        "cart_ref": cart_ref,
                    },
                )
            else:
                key = f"single:{order.id}"
                group = groups.setdefault(
                    key,
                    {"kind": "single", "delivery": None, "orders": [], "latest": order.created_at},
                )
        group["orders"].append(order)
        if order.created_at > group["latest"]:
            group["latest"] = order.created_at

    grouped = list(groups.values())
    grouped.sort(key=lambda item: item["latest"], reverse=True)
    return grouped


async def _group_confirmable_orders(order: Order, buyer_id: int, session: AsyncSession) -> list[Order]:
    delivery = await _effective_delivery(order, session)
    if not delivery:
        return [order]

    result = await session.execute(
        select(Order)
        .options(selectinload(Order.listing))
        .join(DeliveryOrder, DeliveryOrder.order_id == Order.id)
        .where(DeliveryOrder.delivery_id == delivery.id)
        .where(Order.buyer_id == buyer_id)
        .where(Order.status == OrderStatus.PAID)
        .where(Order.delivered_at.is_not(None))
        .order_by(DeliveryOrder.sequence.asc(), Order.id.asc())
    )
    grouped = result.scalars().all()
    return grouped or [order]


def _group_confirm_keyboard(orders: list[Order]) -> InlineKeyboardMarkup:
    rows = []
    for order in orders:
        title = order.listing.title if order.listing else "Item"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"Confirm #{order.id} ({title})",
                    callback_data=f"order_confirm_item_{order.id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="Back to Orders", callback_data="my_orders")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _delivery_group_actions_keyboard(delivery_id: int, fallback_order_id: int, can_confirm: bool) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="Track Delivery", callback_data=f"order_track_{fallback_order_id}")]]
    if can_confirm:
        rows.append([InlineKeyboardButton(text="Confirm Receipt", callback_data=f"order_confirm_group_{delivery_id}")])
    rows.append([InlineKeyboardButton(text="Raise Dispute", callback_data=f"order_dispute_{fallback_order_id}")])
    rows.append([InlineKeyboardButton(text="Back to Orders", callback_data="my_orders")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _cart_group_actions_keyboard(fallback_order_id: int, can_retry: bool) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="Track Delivery", callback_data=f"order_track_{fallback_order_id}")]]
    if can_retry:
        rows.append([InlineKeyboardButton(text="Re-attempt Payment", callback_data=f"order_retry_group_{fallback_order_id}")])
    rows.append([InlineKeyboardButton(text="Raise Dispute", callback_data=f"order_dispute_{fallback_order_id}")])
    rows.append([InlineKeyboardButton(text="Back to Orders", callback_data="my_orders")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _single_order_actions_keyboard(order: Order) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="Track Delivery", callback_data=f"order_track_{order.id}")]]
    if order.status in {OrderStatus.PENDING, OrderStatus.CANCELLED}:
        rows.append([InlineKeyboardButton(text="Re-attempt Payment", callback_data=f"order_retry_payment_{order.id}")])
    else:
        rows.append([InlineKeyboardButton(text="Confirm Receipt", callback_data=f"order_confirm_{order.id}")])
    rows.append([InlineKeyboardButton(text="Raise Dispute", callback_data=f"order_dispute_{order.id}")])
    rows.append([InlineKeyboardButton(text="Back to Orders", callback_data="my_orders")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _resolve_customer_email(user: User, buyer_telegram_id: str) -> str:
    if user.username and "@" in user.username:
        return user.username.strip().lower()
    return f"user_{buyer_telegram_id}@vendoor.app"


def _order_bundle_timeline(orders: list[Order], delivery: Delivery | None) -> str:
    paid_done = any(order.paid_at is not None or order.status in {OrderStatus.PAID, OrderStatus.COMPLETED} for order in orders)
    picked_done = bool(delivery and delivery.status in {DeliveryStatus.PICKED_UP, DeliveryStatus.IN_TRANSIT, DeliveryStatus.DELIVERED})
    in_transit_done = bool(delivery and delivery.status in {DeliveryStatus.IN_TRANSIT, DeliveryStatus.DELIVERED})
    delivered_done = all(order.delivered_at is not None for order in orders)
    confirmed_done = all(order.status == OrderStatus.COMPLETED for order in orders)

    def _mark(done: bool) -> str:
        return "DONE" if done else "PENDING"

    lines = [
        "<b>Timeline</b>",
        f"- Paid: {_mark(paid_done)}",
        f"- Picked Up: {_mark(picked_done)}",
        f"- In Transit: {_mark(in_transit_done)}",
        f"- Delivered: {_mark(delivered_done)}",
        f"- Confirmed: {_mark(confirmed_done)}",
    ]
    return "\n".join(lines)


async def _release_order_and_notify(callback: CallbackQuery, session: AsyncSession, order: Order) -> bool:
    escrow_service = get_escrow_service()
    released = await escrow_service.release_escrow(order.id, session)
    if not released:
        return False

    order.auto_release_scheduled_at = None
    await session.commit()

    seller_user = order.seller.user if order.seller else None
    if seller_user and seller_user.telegram_id:
        try:
            await callback.bot.send_message(
                chat_id=int(seller_user.telegram_id),
                text=(
                    f"Order #{order.id} has been confirmed by the buyer.\n"
                    f"Escrow released: NGN {order.amount:,.2f}."
                ),
            )
        except Exception:
            pass

    return True


async def _confirm_single_order_receipt(callback: CallbackQuery, session: AsyncSession, order: Order) -> None:
    released = await _release_order_and_notify(callback, session, order)
    if not released:
        await safe_replace_with_screen(
            callback,
            "Could not release seller payment right now. Please try again.",
            reply_markup=get_main_menu_inline(),
        )
        return

    await safe_replace_with_screen(
        callback,
        "<b>Receipt Confirmed</b>\n\n"
        "Thank you for shopping with VenDOOR.\n"
        f"Seller has been paid NGN {order.amount:,.2f}\n\n"
        f"Order #{order.id} complete.",
        parse_mode="HTML",
        reply_markup=get_main_menu_inline(),
    )


@router.callback_query(F.data == "my_orders")
async def my_orders(callback: CallbackQuery, session: AsyncSession):
    buyer_id_str = str(callback.from_user.id)

    result = await session.execute(select(User).where(User.telegram_id == buyer_id_str))
    user = result.scalars().first()
    if not user:
        await safe_answer_callback(callback, text="User not found", show_alert=True)
        return

    await safe_answer_callback(callback)

    result = await session.execute(
        select(Order)
        .options(
            selectinload(Order.listing),
            selectinload(Order.delivery),
            selectinload(Order.seller).selectinload(SellerProfile.user),
        )
        .where(Order.buyer_id == user.id)
        .order_by(Order.created_at.desc())
    )
    orders = result.scalars().all()

    if not orders:
        empty_image = get_empty_state("no_orders")
        empty_text = "You have not placed any orders yet.\n\nStart shopping now!"
        await safe_replace_with_screen(
            callback,
            empty_text,
            photo=empty_image,
            reply_markup=get_main_menu_inline(),
        )
        return

    grouped_orders = await _group_orders_by_delivery(orders, session)

    text = "<b>Your Orders</b>\n\n"
    for group in grouped_orders[:5]:
        group_orders: list[Order] = group["orders"]
        total_amount = sum(order.amount for order in group_orders)
        total_items = sum(order.quantity for order in group_orders)
        first_order = group_orders[0]
        effective_delivery = group["delivery"]
        group_status = "PAID"
        if any(order.status == OrderStatus.PENDING for order in group_orders):
            group_status = "PENDING"
        if any(order.status == OrderStatus.DISPUTED for order in group_orders):
            group_status = "DISPUTED"
        elif all(order.status == OrderStatus.COMPLETED for order in group_orders):
            group_status = "COMPLETED"
        elif any(order.status == OrderStatus.CANCELLED for order in group_orders):
            group_status = "CANCELLED"
        elif any(order.status == OrderStatus.REFUNDED for order in group_orders):
            group_status = "REFUNDED"
        status_emoji = {
            "PENDING": "PENDING",
            "PAID": "PAID",
            "COMPLETED": "COMPLETED",
            "DISPUTED": "DISPUTED",
            "CANCELLED": "CANCELLED",
            "REFUNDED": "REFUNDED",
        }.get(group_status, "UNKNOWN")

        title = (
            first_order.listing.title if len(group_orders) == 1 and first_order.listing else f"{len(group_orders)} items"
        )
        if effective_delivery:
            group_label = f"Delivery #{effective_delivery.id}"
        elif group.get("kind") == "cart":
            group_label = f"Order Bundle #{first_order.id}"
        else:
            group_label = f"Order #{first_order.id}"

        text += (
            f"<b>{group_label}</b>\n"
            f"Product(s): {title}\n"
            f"Qty: {total_items}\n"
            f"Amount: NGN {total_amount:,.2f}\n"
            f"Status: {status_emoji}\n"
            f"Delivery: {effective_delivery.status.value if effective_delivery else 'PENDING_ASSIGNMENT'}\n"
            f"Date: {group['latest'].strftime('%d/%m/%Y')}\n\n"
        )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=(
            [
                [
                    InlineKeyboardButton(
                        text=(
                            f"Delivery #{group['delivery'].id}"
                            if group["delivery"]
                            else (
                                f"Order Bundle #{group['orders'][0].id}"
                                if group.get("kind") == "cart"
                                else f"Order #{group['orders'][0].id}"
                            )
                        ),
                        callback_data=(
                            f"view_delivery_group_{group['delivery'].id}"
                            if group["delivery"]
                            else (
                                f"view_cart_group_{group['orders'][0].id}"
                                if group.get("kind") == "cart"
                                else f"view_order_{group['orders'][0].id}"
                            )
                        ),
                    )
                ]
                for group in grouped_orders[:5]
            ]
            + [[InlineKeyboardButton(text="Back", callback_data="back_to_menu")]]
        )
    )

    await safe_replace_with_screen(callback, text, parse_mode="HTML", reply_markup=keyboard)


@router.callback_query(F.data.startswith("view_cart_group_"))
async def view_cart_group(callback: CallbackQuery, session: AsyncSession):
    order_id = _callback_int_suffix(callback.data, "view_cart_group_")
    if order_id is None:
        await safe_answer_callback(callback, text="Invalid order bundle selection", show_alert=True)
        return

    user_result = await session.execute(select(User).where(User.telegram_id == str(callback.from_user.id)))
    user = user_result.scalars().first()
    if not user:
        await safe_answer_callback(callback, text="User not found", show_alert=True)
        return

    seed_result = await session.execute(
        select(Order)
        .options(selectinload(Order.listing), selectinload(Order.seller).selectinload(SellerProfile.user))
        .where(Order.id == order_id)
    )
    seed_order = seed_result.scalars().first()
    if not seed_order or seed_order.buyer_id != user.id:
        await safe_answer_callback(callback, text="Order bundle not found", show_alert=True)
        return

    cart_ref = _cart_group_ref(seed_order)
    if not cart_ref:
        await safe_answer_callback(callback, text="This order is not part of a bundle", show_alert=True)
        return

    result = await session.execute(
        select(Order)
        .options(selectinload(Order.listing), selectinload(Order.seller).selectinload(SellerProfile.user))
        .where(Order.buyer_id == user.id)
        .where(Order.transaction_ref.like(f"{cart_ref}%"))
        .order_by(Order.created_at.asc(), Order.id.asc())
    )
    orders = result.scalars().all()
    if not orders:
        await safe_answer_callback(callback, text="Order bundle not found", show_alert=True)
        return

    await safe_answer_callback(callback)
    total_items = sum(order.quantity for order in orders)
    total_amount = sum(order.amount for order in orders)
    group_status = "PAID"
    if any(order.status == OrderStatus.PENDING for order in orders):
        group_status = "PENDING"
    if any(order.status == OrderStatus.DISPUTED for order in orders):
        group_status = "DISPUTED"
    elif all(order.status == OrderStatus.COMPLETED for order in orders):
        group_status = "COMPLETED"
    elif any(order.status == OrderStatus.CANCELLED for order in orders):
        group_status = "CANCELLED"
    elif any(order.status == OrderStatus.REFUNDED for order in orders):
        group_status = "REFUNDED"

    lines = [
        f"<b>Order Bundle #{orders[0].id}</b>",
        "",
        f"<b>Total Items:</b> {total_items}",
        f"<b>Total Amount:</b> NGN {total_amount:,.2f}",
        f"<b>Status:</b> {group_status}",
        "",
        "<b>Items</b>",
    ]
    for order in orders:
        seller_name = order.seller.user.first_name if order.seller and order.seller.user else "Unknown seller"
        item_title = order.listing.title if order.listing else "Unknown item"
        lines.append(f"#{order.id} - {order.quantity} x {item_title} ({seller_name})")
    lines.append("")
    lines.append(_order_bundle_timeline(orders, None))
    can_retry = (
        any(order.status in {OrderStatus.PENDING, OrderStatus.CANCELLED} for order in orders)
        and not any(order.status in {OrderStatus.PAID, OrderStatus.COMPLETED, OrderStatus.REFUNDED, OrderStatus.DISPUTED} for order in orders)
    )

    await safe_replace_with_screen(
        callback,
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=_cart_group_actions_keyboard(orders[0].id, can_retry=can_retry),
    )


@router.callback_query(F.data.startswith("view_delivery_group_"))
async def view_delivery_group(callback: CallbackQuery, session: AsyncSession):
    delivery_id = _callback_int_suffix(callback.data, "view_delivery_group_")
    if delivery_id is None:
        await safe_answer_callback(callback, text="Invalid delivery selection", show_alert=True)
        return

    user_result = await session.execute(select(User).where(User.telegram_id == str(callback.from_user.id)))
    user = user_result.scalars().first()
    if not user:
        await safe_answer_callback(callback, text="User not found", show_alert=True)
        return

    result = await session.execute(
        select(Order)
        .options(
            selectinload(Order.listing),
            selectinload(Order.seller).selectinload(SellerProfile.user),
        )
        .join(DeliveryOrder, DeliveryOrder.order_id == Order.id)
        .where(DeliveryOrder.delivery_id == delivery_id)
        .where(Order.buyer_id == user.id)
        .order_by(DeliveryOrder.sequence.asc(), Order.id.asc())
    )
    orders = result.scalars().all()
    if not orders:
        await safe_answer_callback(callback, text="Delivery not found", show_alert=True)
        return

    delivery = await _effective_delivery(orders[0], session)
    await safe_answer_callback(callback)

    total_items = sum(order.quantity for order in orders)
    total_amount = sum(order.amount for order in orders)
    text = [
        f"<b>Combined Order • Delivery #{delivery_id}</b>",
        "",
        f"<b>Total Items:</b> {total_items}",
        f"<b>Total Amount:</b> NGN {total_amount:,.2f}",
        f"<b>Delivery Status:</b> {delivery.status.value if delivery else 'PENDING_ASSIGNMENT'}",
        "",
        "<b>Items</b>",
    ]
    for order in orders:
        seller_name = order.seller.user.first_name if order.seller and order.seller.user else "Unknown seller"
        item_title = order.listing.title if order.listing else "Unknown item"
        text.append(f"#{order.id} • {order.quantity} x {item_title} ({seller_name})")
    text.append("")
    text.append(_order_bundle_timeline(orders, delivery))

    can_confirm = any(order.status == OrderStatus.PAID and order.delivered_at for order in orders)
    await safe_replace_with_screen(
        callback,
        "\n".join(text),
        parse_mode="HTML",
        reply_markup=_delivery_group_actions_keyboard(delivery_id, orders[0].id, can_confirm),
    )


@router.callback_query(F.data.startswith("view_order_"))
async def view_order(callback: CallbackQuery, session: AsyncSession):
    order_id = _callback_int_suffix(callback.data, "view_order_")
    if order_id is None:
        await safe_answer_callback(callback, text="Invalid order selection", show_alert=True)
        return

    user_result = await session.execute(select(User).where(User.telegram_id == str(callback.from_user.id)))
    user = user_result.scalars().first()
    if not user:
        await safe_answer_callback(callback, text="User not found", show_alert=True)
        return

    result = await session.execute(
        select(Order)
        .options(
            selectinload(Order.listing),
            selectinload(Order.seller).selectinload(SellerProfile.user),
            selectinload(Order.delivery).selectinload(Delivery.agent),
        )
        .where(Order.id == order_id)
    )
    order = result.scalars().first()

    if not order:
        await safe_answer_callback(callback, text="Order not found", show_alert=True)
        return

    if order.buyer_id != user.id:
        await safe_answer_callback(callback, text="You can only view your own orders", show_alert=True)
        return

    await safe_answer_callback(callback)

    text = (
        f"<b>Order #{order.id}</b>\n\n"
        f"<b>Product:</b> {order.listing.title if order.listing else 'Unknown listing'}\n"
        f"<b>Seller:</b> {order.seller.user.first_name if order.seller and order.seller.user else 'Unknown seller'}\n"
        f"<b>Quantity:</b> {order.quantity}\n"
        f"<b>Amount:</b> NGN {order.amount:,.2f}\n"
        f"<b>Status:</b> {order.status.value}\n\n"
        f"<b>Delivery Address:</b>\n{order.buyer_address}\n"
    )

    if order.buyer_delivery_details:
        text += f"\n<b>Special Instructions:</b>\n{order.buyer_delivery_details}\n"

    effective_delivery = await _effective_delivery(order, session)
    if effective_delivery:
        text += f"\n<b>Delivery Status:</b> {effective_delivery.status.value}\n"
    if order.delivery_eta_at:
        text += f"<b>ETA:</b> {order.delivery_eta_at.strftime('%d/%m/%Y %H:%M')}\n"
    text += f"\n<b>Date:</b> {order.created_at.strftime('%d/%m/%Y %H:%M')}"

    await safe_replace_with_screen(
        callback,
        text,
        parse_mode="HTML",
        reply_markup=_single_order_actions_keyboard(order),
    )


@router.callback_query(F.data.regexp(r"^order_confirm_\d+$"))
async def confirm_receipt(callback: CallbackQuery, session: AsyncSession):
    order_id = _callback_int_suffix(callback.data, "order_confirm_")
    if order_id is None:
        await safe_answer_callback(callback, text="Invalid order confirmation", show_alert=True)
        return

    user_result = await session.execute(select(User).where(User.telegram_id == str(callback.from_user.id)))
    user = user_result.scalars().first()
    if not user:
        await safe_answer_callback(callback, text="User not found", show_alert=True)
        return

    result = await session.execute(
        select(Order)
        .options(selectinload(Order.seller).selectinload(SellerProfile.user))
        .where(Order.id == order_id)
    )
    order = result.scalars().first()

    if not order:
        await safe_answer_callback(callback, text="Order not found", show_alert=True)
        return

    if order.buyer_id != user.id:
        await safe_answer_callback(callback, text="You can only confirm your own orders", show_alert=True)
        return

    if order.status != OrderStatus.PAID:
        await safe_answer_callback(
            callback,
            text=f"Cannot confirm receipt. Order status is {order.status.value}",
            show_alert=True,
        )
        return
    if not order.delivered_at:
        await safe_answer_callback(
            callback,
            text="Order has not been marked delivered yet.",
            show_alert=True,
        )
        return

    await safe_answer_callback(callback)
    grouped_orders = await _group_confirmable_orders(order, user.id, session)
    if len(grouped_orders) > 1:
        text = ["<b>Confirm Delivered Items</b>", ""]
        for grouped_order in grouped_orders:
            item_title = grouped_order.listing.title if grouped_order.listing else "Item"
            text.append(f"Order #{grouped_order.id}: {grouped_order.quantity} x {item_title}")
        text.append("")
        text.append("Tap each button below to confirm receipt item-by-item.")
        await safe_replace_with_screen(
            callback,
            "\n".join(text),
            parse_mode="HTML",
            reply_markup=_group_confirm_keyboard(grouped_orders),
        )
        return

    try:
        await _confirm_single_order_receipt(callback, session, order)
    except Exception:
        await session.rollback()
        await safe_replace_with_screen(callback, "Could not confirm receipt right now. Please try again.")


@router.callback_query(F.data.regexp(r"^order_confirm_group_\d+$"))
async def confirm_receipt_group(callback: CallbackQuery, session: AsyncSession):
    delivery_id = _callback_int_suffix((callback.data or "").replace("order_confirm_group_", "view_delivery_group_"), "view_delivery_group_")
    if delivery_id is None:
        await safe_answer_callback(callback, text="Invalid order confirmation", show_alert=True)
        return

    user_result = await session.execute(select(User).where(User.telegram_id == str(callback.from_user.id)))
    user = user_result.scalars().first()
    if not user:
        await safe_answer_callback(callback, text="User not found", show_alert=True)
        return

    result = await session.execute(
        select(Order)
        .options(selectinload(Order.listing))
        .join(DeliveryOrder, DeliveryOrder.order_id == Order.id)
        .where(DeliveryOrder.delivery_id == delivery_id)
        .where(Order.buyer_id == user.id)
        .where(Order.status == OrderStatus.PAID)
        .where(Order.delivered_at.is_not(None))
        .order_by(DeliveryOrder.sequence.asc(), Order.id.asc())
    )
    grouped_orders = result.scalars().all()
    if not grouped_orders:
        await safe_answer_callback(callback, text="No delivered items to confirm yet.", show_alert=True)
        return

    await safe_answer_callback(callback)
    text = ["<b>Confirm Delivered Items</b>", ""]
    for grouped_order in grouped_orders:
        item_title = grouped_order.listing.title if grouped_order.listing else "Item"
        text.append(f"Order #{grouped_order.id}: {grouped_order.quantity} x {item_title}")
    text.append("")
    text.append("Tap each button below to confirm receipt item-by-item.")
    await safe_replace_with_screen(
        callback,
        "\n".join(text),
        parse_mode="HTML",
        reply_markup=_group_confirm_keyboard(grouped_orders),
    )


@router.callback_query(F.data.regexp(r"^order_confirm_item_\d+$"))
async def confirm_receipt_group_item(callback: CallbackQuery, session: AsyncSession):
    order_id = _callback_int_suffix((callback.data or "").replace("order_confirm_item_", "order_confirm_"), "order_confirm_")
    if order_id is None:
        await safe_answer_callback(callback, text="Invalid order confirmation", show_alert=True)
        return

    user_result = await session.execute(select(User).where(User.telegram_id == str(callback.from_user.id)))
    user = user_result.scalars().first()
    if not user:
        await safe_answer_callback(callback, text="User not found", show_alert=True)
        return

    result = await session.execute(
        select(Order)
        .options(selectinload(Order.seller).selectinload(SellerProfile.user), selectinload(Order.listing))
        .where(Order.id == order_id)
    )
    order = result.scalars().first()
    if not order:
        await safe_answer_callback(callback, text="Order not found", show_alert=True)
        return
    if order.buyer_id != user.id:
        await safe_answer_callback(callback, text="You can only confirm your own orders", show_alert=True)
        return
    if order.status != OrderStatus.PAID or not order.delivered_at:
        await safe_answer_callback(callback, text="This order is not ready for receipt confirmation.", show_alert=True)
        return

    await safe_answer_callback(callback)
    try:
        released = await _release_order_and_notify(callback, session, order)
        if not released:
            await safe_replace_with_screen(
                callback,
                "Could not release seller payment right now. Please try again.",
                reply_markup=get_main_menu_inline(),
            )
            return

        remaining = await _group_confirmable_orders(order, user.id, session)
        if len(remaining) > 1 or (len(remaining) == 1 and remaining[0].id != order.id):
            text = [f"<b>Receipt Confirmed for Order #{order.id}</b>", ""]
            text.append("Confirm remaining delivered items below:")
            text.append("")
            for grouped_order in remaining:
                item_title = grouped_order.listing.title if grouped_order.listing else "Item"
                text.append(f"Order #{grouped_order.id}: {grouped_order.quantity} x {item_title}")
            await safe_replace_with_screen(
                callback,
                "\n".join(text),
                parse_mode="HTML",
                reply_markup=_group_confirm_keyboard(remaining),
            )
            return

        await safe_replace_with_screen(
            callback,
            "<b>All Delivered Items Confirmed</b>\n\nThank you for shopping with VenDOOR.",
            parse_mode="HTML",
            reply_markup=get_main_menu_inline(),
        )
    except Exception:
        await session.rollback()
        await safe_replace_with_screen(callback, "Could not confirm receipt right now. Please try again.")


@router.callback_query(F.data.regexp(r"^order_retry_payment_\d+$"))
async def retry_single_order_payment(callback: CallbackQuery, session: AsyncSession):
    order_id = _callback_int_suffix((callback.data or "").replace("order_retry_payment_", "view_order_"), "view_order_")
    if order_id is None:
        await safe_answer_callback(callback, text="Invalid retry request", show_alert=True)
        return

    buyer_result = await session.execute(select(User).where(User.telegram_id == str(callback.from_user.id)))
    buyer = buyer_result.scalars().first()
    if not buyer:
        await safe_answer_callback(callback, text="User not found", show_alert=True)
        return

    result = await session.execute(
        select(Order)
        .options(selectinload(Order.listing))
        .where(Order.id == order_id)
    )
    order = result.scalars().first()
    if not order or order.buyer_id != buyer.id:
        await safe_answer_callback(callback, text="Order not found", show_alert=True)
        return
    if order.status not in {OrderStatus.PENDING, OrderStatus.CANCELLED}:
        await safe_answer_callback(callback, text=f"Order is {order.status.value}; retry not allowed.", show_alert=True)
        return
    if not order.listing or not order.listing.available or order.listing.quantity < order.quantity:
        await safe_answer_callback(callback, text="Item is out of stock for retry.", show_alert=True)
        return

    await safe_answer_callback(callback)
    reference = f"VENDOOR_RETRY_{order.id}_{buyer.telegram_id}_{int(datetime.utcnow().timestamp())}"[:255]
    korapay = get_korapay_client()
    checkout = await korapay.initialize_charge(
        amount=order.amount,
        reference=reference,
        customer_email=_resolve_customer_email(buyer, str(callback.from_user.id)),
        customer_name=buyer.first_name,
        callback_url=f"{settings.api_host}/webhooks/korapay",
    )
    if not checkout:
        await safe_replace_with_screen(
            callback,
            "Could not initialize payment retry right now. Please try again shortly.",
            reply_markup=get_main_menu_inline(),
        )
        return

    order.transaction_ref = reference
    order.status = OrderStatus.PENDING
    order.paid_at = None
    await session.commit()

    await safe_replace_with_screen(
        callback,
        (
            "<b>Retry Payment</b>\n\n"
            f"Order #{order.id}\n"
            f"Amount: NGN {order.amount:,.2f}\n\n"
            "Tap below to complete payment."
        ),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Pay Now", url=checkout.checkout_url)],
                [InlineKeyboardButton(text="Back to Orders", callback_data="my_orders")],
            ]
        ),
    )


@router.callback_query(F.data.regexp(r"^order_retry_group_\d+$"))
async def retry_cart_group_payment(callback: CallbackQuery, session: AsyncSession):
    seed_order_id = _callback_int_suffix((callback.data or "").replace("order_retry_group_", "view_cart_group_"), "view_cart_group_")
    if seed_order_id is None:
        await safe_answer_callback(callback, text="Invalid retry bundle request", show_alert=True)
        return

    buyer_result = await session.execute(select(User).where(User.telegram_id == str(callback.from_user.id)))
    buyer = buyer_result.scalars().first()
    if not buyer:
        await safe_answer_callback(callback, text="User not found", show_alert=True)
        return

    seed_result = await session.execute(select(Order).where(Order.id == seed_order_id))
    seed_order = seed_result.scalars().first()
    if not seed_order or seed_order.buyer_id != buyer.id:
        await safe_answer_callback(callback, text="Order bundle not found", show_alert=True)
        return
    cart_ref = _cart_group_ref(seed_order)
    if not cart_ref:
        await safe_answer_callback(callback, text="This order is not part of a cart bundle", show_alert=True)
        return

    result = await session.execute(
        select(Order)
        .options(selectinload(Order.listing))
        .where(Order.buyer_id == buyer.id)
        .where(Order.transaction_ref.like(f"{cart_ref}%"))
        .order_by(Order.created_at.asc(), Order.id.asc())
    )
    bundle_orders = result.scalars().all()
    if not bundle_orders:
        await safe_answer_callback(callback, text="Bundle orders not found", show_alert=True)
        return

    retry_orders = [order for order in bundle_orders if order.status in {OrderStatus.PENDING, OrderStatus.CANCELLED}]
    if not retry_orders:
        await safe_answer_callback(callback, text="No retry-eligible orders in this bundle.", show_alert=True)
        return
    if any(order.status in {OrderStatus.PAID, OrderStatus.COMPLETED, OrderStatus.DISPUTED, OrderStatus.REFUNDED} for order in bundle_orders):
        await safe_answer_callback(callback, text="Bundle has active/closed orders; retry each order separately.", show_alert=True)
        return

    required_by_listing: dict[int, int] = {}
    for order in retry_orders:
        required_by_listing[order.listing_id] = required_by_listing.get(order.listing_id, 0) + order.quantity
    listings_result = await session.execute(
        select(Order)
        .options(selectinload(Order.listing))
        .where(Order.id.in_([order.id for order in retry_orders]))
    )
    listing_holder = listings_result.scalars().all()
    listing_map = {order.id: order.listing for order in listing_holder}
    for order in retry_orders:
        listing = listing_map.get(order.id)
        if not listing or not listing.available or listing.quantity < required_by_listing.get(listing.id, order.quantity):
            await safe_answer_callback(callback, text="One or more items are out of stock.", show_alert=True)
            return

    await safe_answer_callback(callback)
    order_ids = [str(order.id) for order in retry_orders]
    cart_reference = f"VENDOOR_CART_{buyer.telegram_id}_{int(datetime.utcnow().timestamp())}_{'-'.join(order_ids)}"
    korapay = get_korapay_client()
    checkout = await korapay.initialize_charge(
        amount=sum(order.amount for order in retry_orders),
        reference=cart_reference,
        customer_email=_resolve_customer_email(buyer, str(callback.from_user.id)),
        customer_name=buyer.first_name,
        callback_url=f"{settings.api_host}/webhooks/korapay",
    )
    if not checkout:
        await safe_replace_with_screen(
            callback,
            "Could not initialize bundle payment retry right now. Please try again shortly.",
            reply_markup=get_main_menu_inline(),
        )
        return

    for idx, order in enumerate(retry_orders):
        order.status = OrderStatus.PENDING
        order.paid_at = None
        order.transaction_ref = cart_reference if idx == 0 else f"{cart_reference}_{order.id}"[:255]
    await session.commit()

    await safe_replace_with_screen(
        callback,
        (
            "<b>Bundle Retry Payment</b>\n\n"
            f"Orders: {len(retry_orders)}\n"
            f"Total: NGN {sum(order.amount for order in retry_orders):,.2f}\n\n"
            "Tap below to pay once for all items."
        ),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Pay Bundle Now", url=checkout.checkout_url)],
                [InlineKeyboardButton(text="Back to Orders", callback_data="my_orders")],
            ]
        ),
    )


@router.callback_query(F.data.startswith("order_track_"))
async def track_order(callback: CallbackQuery, session: AsyncSession):
    order_id = _callback_int_suffix(callback.data, "order_track_")
    if order_id is None:
        await safe_answer_callback(callback, text="Invalid order tracking request", show_alert=True)
        return

    user_result = await session.execute(select(User).where(User.telegram_id == str(callback.from_user.id)))
    user = user_result.scalars().first()
    if not user:
        await safe_answer_callback(callback, text="User not found", show_alert=True)
        return

    result = await session.execute(
        select(Order)
        .options(
            selectinload(Order.listing),
            selectinload(Order.seller).selectinload(SellerProfile.user),
            selectinload(Order.delivery).selectinload(Delivery.agent),
        )
        .where(Order.id == order_id)
    )
    order = result.scalars().first()
    if not order:
        await safe_answer_callback(callback, text="Order not found", show_alert=True)
        return
    if order.buyer_id != user.id:
        await safe_answer_callback(callback, text="You can only track your own orders", show_alert=True)
        return

    await safe_answer_callback(callback)

    effective_delivery = await _effective_delivery(order, session)
    delivery_status = effective_delivery.status.value if effective_delivery else "PENDING_ASSIGNMENT"
    agent_name = "To be assigned"
    agent_phone = "N/A"
    if effective_delivery and effective_delivery.agent:
        agent_name = effective_delivery.agent.name or "Assigned"
        agent_phone = effective_delivery.agent.phone or "N/A"

    text = (
        f"<b>Track Order #{order.id}</b>\n\n"
        f"<b>Item:</b> {order.listing.title if order.listing else 'Unknown listing'}\n"
        f"<b>Delivery Status:</b> {delivery_status}\n"
        f"<b>Agent:</b> {agent_name}\n"
        f"<b>Phone:</b> {agent_phone}\n"
    )
    if effective_delivery and effective_delivery.current_location_note:
        text += f"<b>Latest Location Note:</b> {effective_delivery.current_location_note}\n"
    if order.delivered_at:
        text += f"<b>Delivered At:</b> {order.delivered_at.strftime('%d/%m/%Y %H:%M')}\n"

    await safe_replace_with_screen(
        callback,
        text,
        parse_mode="HTML",
        reply_markup=_single_order_actions_keyboard(order),
    )
