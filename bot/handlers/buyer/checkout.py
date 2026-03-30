"""
Buyer checkout FSM for purchase transactions.
Collects delivery details and initiates payment.
"""

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.helpers.telegram import safe_answer_callback, safe_edit_text
from bot.keyboards.main_menu import get_main_menu_inline
from core.config import get_settings
from db.models import Listing, Order, OrderStatus, User
from services.korapay import get_korapay_client

router = Router()
settings = get_settings()


class CheckoutStates(StatesGroup):
    awaiting_delivery_address = State()
    awaiting_delivery_details = State()
    confirming_order = State()


async def start_checkout(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    listing_id = data.get("listing_id")

    result = await session.execute(select(Listing).where(Listing.id == listing_id))
    listing = result.scalars().first()

    if not listing:
        await safe_answer_callback(callback, text="Listing not found", show_alert=True)
        return
    if not listing.available or listing.quantity <= 0:
        await safe_answer_callback(callback, text="This item is out of stock", show_alert=True)
        return

    await safe_answer_callback(callback)

    text = (
        f"<b>{listing.title}</b>\n\n"
        f"Price: NGN {listing.buyer_price:,.2f}\n"
        f"Quantity Left: {listing.quantity}\n"
        "(Includes 5% platform fee)\n\n"
        "<b>Escrow Protection</b>\n"
        "Your payment is safe. Seller gets paid only after you confirm receipt.\n\n"
        "What is your delivery address?"
    )
    if callback.message:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer(text, parse_mode="HTML")
    else:
        await callback.bot.send_message(chat_id=callback.from_user.id, text=text, parse_mode="HTML")
    await state.set_state(CheckoutStates.awaiting_delivery_address)


@router.message(CheckoutStates.awaiting_delivery_address)
async def handle_delivery_address(message: Message, state: FSMContext):
    address = message.text.strip()

    if len(address) < 10:
        await message.reply("Please provide a full delivery address.")
        return

    await state.update_data(delivery_address=address)

    await message.answer(
        "<b>Delivery Details</b>\n\n"
        "Any special instructions for delivery?\n"
        "(e.g., 'Leave at front desk', 'Call before arriving')\n\n"
        "Or type 'None' if there are no special instructions.",
        parse_mode="HTML",
    )
    await state.set_state(CheckoutStates.awaiting_delivery_details)


@router.message(CheckoutStates.awaiting_delivery_details)
async def handle_delivery_details(message: Message, state: FSMContext, session: AsyncSession):
    details = message.text.strip()
    if details.lower() == "none":
        details = None

    await state.update_data(delivery_details=details)
    data = await state.get_data()

    listing_id = data.get("listing_id")

    result = await session.execute(select(Listing).where(Listing.id == listing_id))
    listing = result.scalars().first()
    if not listing or not listing.available or listing.quantity <= 0:
        await message.answer("This listing is currently out of stock.")
        await state.clear()
        return

    text = (
        "<b>Order Confirmation</b>\n\n"
        f"<b>Product:</b> {listing.title}\n"
        f"<b>Price:</b> NGN {listing.buyer_price:,.2f}\n\n"
        f"<b>Delivery To:</b>\n{data.get('delivery_address')}\n\n"
    )

    if data.get("delivery_details"):
        text += f"<b>Special Instructions:</b>\n{data.get('delivery_details')}\n\n"

    text += (
        "<b>Payment is protected by escrow</b>\n"
        "Confirm receipt after receiving the item, then money is released to seller."
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Proceed to Payment", callback_data="proceed_payment")],
            [InlineKeyboardButton(text="Cancel", callback_data="back_to_menu")],
        ]
    )

    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)
    await state.set_state(CheckoutStates.confirming_order)


@router.callback_query(F.data == "proceed_payment", StateFilter(CheckoutStates.confirming_order))
async def proceed_to_payment(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    buyer_telegram_id = str(callback.from_user.id)
    listing_id = data.get("listing_id")
    await safe_answer_callback(callback)

    try:
        result = await session.execute(select(User).where(User.telegram_id == buyer_telegram_id))
        buyer = result.scalars().first()

        result = await session.execute(select(Listing).where(Listing.id == listing_id))
        listing = result.scalars().first()
        if not listing or not listing.available or listing.quantity <= 0:
            await safe_edit_text(
                callback,
                "This item is out of stock now. Please choose another listing.",
                reply_markup=get_main_menu_inline(),
            )
            await state.clear()
            return

        order = Order(
            buyer_id=buyer.id,
            seller_id=listing.seller_id,
            listing_id=listing.id,
            amount=listing.buyer_price,
            status=OrderStatus.PENDING,
            buyer_address=data.get("delivery_address"),
            buyer_delivery_details=data.get("delivery_details"),
        )
        session.add(order)
        await session.flush()

        korapay = get_korapay_client()
        reference = f"VENDOOR_{order.id}_{buyer_telegram_id}"

        korapay_ref = await korapay.initialize_charge(
            amount=order.amount,
            reference=reference,
            customer_email=buyer.username or f"user_{buyer_telegram_id}@vendoor.local",
            customer_name=buyer.first_name,
            callback_url=f"{settings.api_host}/webhooks/korapay",
        )

        if korapay_ref:
            order.transaction_ref = reference
            await session.commit()

            text = (
                "<b>Payment Link</b>\n\n"
                f"Click below to complete payment of NGN {order.amount:,.2f}\n\n"
                f"Order ID: {order.id}"
            )

            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="Pay Now", url=korapay_ref.checkout_url)],
                ]
            )

            await safe_edit_text(callback, text, parse_mode="HTML", reply_markup=keyboard)
        else:
            await session.rollback()
            await safe_edit_text(
                callback,
                "Payment initialization failed. Please try again.",
                reply_markup=get_main_menu_inline(),
            )

    except Exception as e:
        await session.rollback()
        await safe_edit_text(callback, f"Error: {e}", reply_markup=get_main_menu_inline())

    await state.clear()
