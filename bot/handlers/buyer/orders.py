"""
Buyer orders handler - view orders, confirm receipt, raise disputes.
"""

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.helpers.brand_assets import get_empty_state
from bot.helpers.telegram import safe_answer_callback, safe_replace_with_screen
from bot.keyboards.main_menu import get_main_menu_inline, get_order_actions
from db.models import Delivery, DeliveryOrder, Order, OrderStatus, SellerProfile, User
from services.escrow import get_escrow_service

router = Router()


def _callback_int_suffix(callback_data: str | None, prefix: str) -> int | None:
    payload = (callback_data or "").strip()
    if not payload.startswith(prefix):
        return None
    value = payload.replace(prefix, "", 1).strip()
    if not value.isdigit():
        return None
    return int(value)


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
        group_label = f"Delivery #{effective_delivery.id}" if effective_delivery else f"Order #{first_order.id}"

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
                        text=(f"Delivery #{group['delivery'].id}" if group["delivery"] else f"Order #{group['orders'][0].id}"),
                        callback_data=(
                            f"view_delivery_group_{group['delivery'].id}"
                            if group["delivery"]
                            else f"view_order_{group['orders'][0].id}"
                        ),
                    )
                ]
                for group in grouped_orders[:5]
            ]
            + [[InlineKeyboardButton(text="Back", callback_data="back_to_menu")]]
        )
    )

    await safe_replace_with_screen(callback, text, parse_mode="HTML", reply_markup=keyboard)


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
        reply_markup=get_order_actions(order.id),
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
        reply_markup=get_order_actions(order.id),
    )
