"""
Buyer orders handler - view orders, confirm receipt, raise disputes.
"""

from aiogram import Router, F
from aiogram.types import CallbackQuery
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Order, User, OrderStatus
from bot.keyboards.main_menu import get_order_actions, get_main_menu_inline

router = Router()


@router.callback_query(F.data == "my_orders")
async def my_orders(callback: CallbackQuery, session: AsyncSession):
    """
    Show user's orders.
    """
    buyer_id_str = str(callback.from_user.id)
    
    # Get user
    result = await session.execute(
        select(User).where(User.telegram_id == buyer_id_str)
    )
    user = result.scalars().first()
    
    if not user:
        await callback.answer("User not found", show_alert=True)
        return
    
    # Get orders
    result = await session.execute(
        select(Order)
        .where(Order.buyer_id == user.id)
        .order_by(Order.created_at.desc())
    )
    orders = result.scalars().all()
    
    if not orders:
        await callback.message.edit_text(
            "📭 You haven't placed any orders yet.\n\n"
            "Start shopping now!",
            reply_markup=get_main_menu_inline(),
        )
        await callback.answer()
        return
    
    # Build orders list
    text = "📦 <b>Your Orders</b>\n\n"
    for order in orders[:5]:  # Show last 5 orders
        status_emoji = {
            OrderStatus.PENDING: "⏳",
            OrderStatus.PAID: "💳",
            OrderStatus.COMPLETED: "✅",
            OrderStatus.DISPUTED: "⚠️",
            OrderStatus.CANCELLED: "❌",
            OrderStatus.REFUNDED: "💰",
        }.get(order.status, "❓")
        
        text += (
            f"{status_emoji} <b>Order #{order.id}</b>\n"
            f"  Product: {order.listing.title}\n"
            f"  Amount: ₦{order.amount:,.2f}\n"
            f"  Status: {order.status.value}\n"
            f"  Date: {order.created_at.strftime('%d/%m/%Y')}\n\n"
        )
    
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"Order #{orders[0].id}", callback_data=f"view_order_{orders[0].id}")],
            [InlineKeyboardButton(text="◀️ Back", callback_data="back_to_menu")],
        ]
    )
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("view_order_"))
async def view_order(callback: CallbackQuery, session: AsyncSession):
    """
    View order details.
    """
    order_id = int(callback.data.replace("view_order_", ""))
    
    user_result = await session.execute(
        select(User).where(User.telegram_id == str(callback.from_user.id))
    )
    user = user_result.scalars().first()
    if not user:
        await callback.answer("User not found", show_alert=True)
        return

    result = await session.execute(select(Order).where(Order.id == order_id))
    order = result.scalars().first()
    
    if not order:
        await callback.answer("Order not found", show_alert=True)
        return

    if order.buyer_id != user.id:
        await callback.answer("You can only view your own orders", show_alert=True)
        return
    
    text = (
        f"📦 <b>Order #{order.id}</b>\n\n"
        f"<b>Product:</b> {order.listing.title}\n"
        f"<b>Seller:</b> {order.seller.user.first_name}\n"
        f"<b>Amount:</b> ₦{order.amount:,.2f}\n"
        f"<b>Status:</b> {order.status.value}\n\n"
        f"<b>Delivery Address:</b>\n{order.buyer_address}\n"
    )
    
    if order.buyer_delivery_details:
        text += f"\n<b>Special Instructions:</b>\n{order.buyer_delivery_details}\n"
    
    text += f"\n<b>Date:</b> {order.created_at.strftime('%d/%m/%Y %H:%M')}"
    
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=get_order_actions(order.id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("order_confirm_"))
async def confirm_receipt(callback: CallbackQuery, session: AsyncSession):
    """
    Confirm receipt - release escrow to seller.
    """
    order_id = int(callback.data.replace("order_confirm_", ""))
    
    user_result = await session.execute(
        select(User).where(User.telegram_id == str(callback.from_user.id))
    )
    user = user_result.scalars().first()
    if not user:
        await callback.answer("User not found", show_alert=True)
        return

    result = await session.execute(select(Order).where(Order.id == order_id))
    order = result.scalars().first()
    
    if not order:
        await callback.answer("Order not found", show_alert=True)
        return

    if order.buyer_id != user.id:
        await callback.answer("You can only confirm your own orders", show_alert=True)
        return
    
    if order.status != OrderStatus.PAID:
        await callback.answer(
            f"Cannot confirm receipt. Order status is {order.status.value}",
            show_alert=True
        )
        return
    
    try:
        # Update order status
        order.status = OrderStatus.COMPLETED
        await session.commit()
        
        await callback.message.edit_text(
            f"✅ <b>Receipt Confirmed!</b>\n\n"
            f"Thank you for shopping with VenDOOR.\n"
            f"Seller has been paid ₦{order.amount:,.2f}\n\n"
            f"Order #{order.id} complete.",
            parse_mode="HTML",
            reply_markup=get_main_menu_inline(),
        )
    except Exception as e:
        await session.rollback()
        await callback.message.edit_text(f"❌ Error: {str(e)}")
    
    await callback.answer()
