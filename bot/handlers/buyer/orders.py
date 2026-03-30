"""
Buyer orders handler - view orders, confirm receipt, raise disputes.
"""

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.helpers.brand_assets import get_empty_state
from bot.helpers.telegram import safe_answer_callback, safe_edit_text
from bot.keyboards.main_menu import get_main_menu_inline, get_order_actions
from db.models import Order, OrderStatus, SellerProfile, User

router = Router()


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
        .options(selectinload(Order.listing))
        .where(Order.buyer_id == user.id)
        .order_by(Order.created_at.desc())
    )
    orders = result.scalars().all()

    if not orders:
        empty_image = get_empty_state("no_orders")
        empty_text = "You have not placed any orders yet.\n\nStart shopping now!"
        if empty_image:
            await callback.message.answer_photo(
                photo=empty_image,
                caption=empty_text,
                reply_markup=get_main_menu_inline(),
            )
        else:
            await safe_edit_text(
                callback,
                empty_text,
                reply_markup=get_main_menu_inline(),
            )
        return

    text = "<b>Your Orders</b>\n\n"
    for order in orders[:5]:
        status_emoji = {
            OrderStatus.PENDING: "PENDING",
            OrderStatus.PAID: "PAID",
            OrderStatus.COMPLETED: "COMPLETED",
            OrderStatus.DISPUTED: "DISPUTED",
            OrderStatus.CANCELLED: "CANCELLED",
            OrderStatus.REFUNDED: "REFUNDED",
        }.get(order.status, "UNKNOWN")

        text += (
            f"<b>Order #{order.id}</b>\n"
            f"Product: {order.listing.title}\n"
            f"Amount: NGN {order.amount:,.2f}\n"
            f"Status: {status_emoji}\n"
            f"Date: {order.created_at.strftime('%d/%m/%Y')}\n\n"
        )

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"Order #{orders[0].id}", callback_data=f"view_order_{orders[0].id}")],
            [InlineKeyboardButton(text="Back", callback_data="back_to_menu")],
        ]
    )

    await safe_edit_text(callback, text, parse_mode="HTML", reply_markup=keyboard)


@router.callback_query(F.data.startswith("view_order_"))
async def view_order(callback: CallbackQuery, session: AsyncSession):
    order_id = int(callback.data.replace("view_order_", ""))

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
        f"<b>Product:</b> {order.listing.title}\n"
        f"<b>Seller:</b> {order.seller.user.first_name}\n"
        f"<b>Amount:</b> NGN {order.amount:,.2f}\n"
        f"<b>Status:</b> {order.status.value}\n\n"
        f"<b>Delivery Address:</b>\n{order.buyer_address}\n"
    )

    if order.buyer_delivery_details:
        text += f"\n<b>Special Instructions:</b>\n{order.buyer_delivery_details}\n"

    text += f"\n<b>Date:</b> {order.created_at.strftime('%d/%m/%Y %H:%M')}"

    await safe_edit_text(callback, text, parse_mode="HTML", reply_markup=get_order_actions(order.id))


@router.callback_query(F.data.startswith("order_confirm_"))
async def confirm_receipt(callback: CallbackQuery, session: AsyncSession):
    order_id = int(callback.data.replace("order_confirm_", ""))

    user_result = await session.execute(select(User).where(User.telegram_id == str(callback.from_user.id)))
    user = user_result.scalars().first()
    if not user:
        await safe_answer_callback(callback, text="User not found", show_alert=True)
        return

    result = await session.execute(select(Order).where(Order.id == order_id))
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

    await safe_answer_callback(callback)

    try:
        order.status = OrderStatus.COMPLETED
        await session.commit()

        await safe_edit_text(
            callback,
            "<b>Receipt Confirmed</b>\n\n"
            "Thank you for shopping with VenDOOR.\n"
            f"Seller has been paid NGN {order.amount:,.2f}\n\n"
            f"Order #{order.id} complete.",
            parse_mode="HTML",
            reply_markup=get_main_menu_inline(),
        )
    except Exception as e:
        await session.rollback()
        await safe_edit_text(callback, f"Error: {e}")
