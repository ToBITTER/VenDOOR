"""
Buyer cart handler - add/remove cart items and trigger cart checkout.
"""

from decimal import Decimal

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.helpers.telegram import safe_answer_callback, safe_replace_with_screen
from bot.keyboards.main_menu import get_main_menu_inline
from db.models import CartItem, Listing, User

router = Router()


def _callback_int_suffix(callback_data: str | None, prefix: str) -> int | None:
    payload = (callback_data or "").strip()
    if not payload.startswith(prefix):
        return None
    value = payload.replace(prefix, "", 1).strip()
    if not value.isdigit():
        return None
    return int(value)


def _cart_actions_keyboard(cart_item_ids: list[int]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"Remove item #{item_id}", callback_data=f"cart_remove_{item_id}")] for item_id in cart_item_ids]
    rows.append([InlineKeyboardButton(text="Checkout Cart", callback_data="cart_checkout")])
    rows.append([InlineKeyboardButton(text="Back to Menu", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("add_to_cart_"))
async def add_to_cart(callback: CallbackQuery, session: AsyncSession):
    listing_id = _callback_int_suffix(callback.data, "add_to_cart_")
    if listing_id is None:
        await safe_answer_callback(callback, text="Invalid listing selection.", show_alert=True)
        return

    buyer_result = await session.execute(select(User).where(User.telegram_id == str(callback.from_user.id)))
    buyer = buyer_result.scalars().first()
    if not buyer:
        await safe_answer_callback(callback, text="Please send /start first.", show_alert=True)
        return

    listing_result = await session.execute(select(Listing).where(Listing.id == listing_id))
    listing = listing_result.scalars().first()
    if not listing or not listing.available or listing.quantity <= 0:
        await safe_answer_callback(callback, text="This item is out of stock.", show_alert=True)
        return

    cart_result = await session.execute(
        select(CartItem)
        .where(CartItem.buyer_id == buyer.id)
        .where(CartItem.listing_id == listing.id)
    )
    cart_item = cart_result.scalars().first()

    if cart_item:
        if cart_item.quantity >= listing.quantity:
            await safe_answer_callback(callback, text="No more stock available for this item.", show_alert=True)
            return
        cart_item.quantity += 1
    else:
        cart_item = CartItem(buyer_id=buyer.id, listing_id=listing.id, quantity=1)
        session.add(cart_item)

    await session.commit()
    await safe_answer_callback(callback, text="Added to cart.")


@router.callback_query(F.data == "my_cart")
async def my_cart(callback: CallbackQuery, session: AsyncSession):
    buyer_result = await session.execute(select(User).where(User.telegram_id == str(callback.from_user.id)))
    buyer = buyer_result.scalars().first()
    if not buyer:
        await safe_answer_callback(callback, text="Please send /start first.", show_alert=True)
        return

    await safe_answer_callback(callback)
    result = await session.execute(
        select(CartItem)
        .options(selectinload(CartItem.listing))
        .where(CartItem.buyer_id == buyer.id)
        .order_by(CartItem.created_at.asc())
    )
    items = result.scalars().all()
    if not items:
        await safe_replace_with_screen(
            callback,
            "Your cart is empty.\n\nBrowse catalog and add items.",
            reply_markup=get_main_menu_inline(),
        )
        return

    lines = ["<b>Your Cart</b>", ""]
    total = Decimal("0")
    item_ids: list[int] = []
    for idx, item in enumerate(items, start=1):
        listing = item.listing
        if not listing:
            continue
        line_total = listing.buyer_price * item.quantity
        total += line_total
        item_ids.append(item.id)
        lines.append(
            f"{idx}. {listing.title}\n"
            f"Qty: {item.quantity}  |  Unit: NGN {listing.buyer_price:,.2f}\n"
            f"Subtotal: NGN {line_total:,.2f}\n"
            f"Cart Item ID: {item.id}"
        )
        lines.append("")

    lines.append(f"<b>Total:</b> NGN {total:,.2f}")
    lines.append("At checkout, orders are split by seller for fulfillment.")

    await safe_replace_with_screen(
        callback,
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=_cart_actions_keyboard(item_ids),
    )


@router.callback_query(F.data.startswith("cart_remove_"))
async def remove_cart_item(callback: CallbackQuery, session: AsyncSession):
    cart_item_id = _callback_int_suffix(callback.data, "cart_remove_")
    if cart_item_id is None:
        await safe_answer_callback(callback, text="Invalid cart item.", show_alert=True)
        return
    buyer_result = await session.execute(select(User).where(User.telegram_id == str(callback.from_user.id)))
    buyer = buyer_result.scalars().first()
    if not buyer:
        await safe_answer_callback(callback, text="Please send /start first.", show_alert=True)
        return

    result = await session.execute(
        select(CartItem)
        .where(CartItem.id == cart_item_id)
        .where(CartItem.buyer_id == buyer.id)
    )
    cart_item = result.scalars().first()
    if not cart_item:
        await safe_answer_callback(callback, text="Cart item not found.", show_alert=True)
        return

    await session.delete(cart_item)
    await session.commit()
    await safe_answer_callback(callback, text="Removed from cart.")
    await my_cart(callback, session)


@router.callback_query(F.data == "cart_checkout")
async def cart_checkout(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    buyer_result = await session.execute(select(User).where(User.telegram_id == str(callback.from_user.id)))
    buyer = buyer_result.scalars().first()
    if not buyer:
        await safe_answer_callback(callback, text="Please send /start first.", show_alert=True)
        return

    result = await session.execute(
        select(CartItem.id)
        .where(CartItem.buyer_id == buyer.id)
    )
    cart_item_ids = [row[0] for row in result.all()]
    if not cart_item_ids:
        await safe_answer_callback(callback, text="Your cart is empty.", show_alert=True)
        return

    await state.update_data(cart_item_ids=cart_item_ids)

    from bot.handlers.buyer import checkout

    await checkout.start_checkout(callback, state, session)
