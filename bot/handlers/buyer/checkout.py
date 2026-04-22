"""
Buyer checkout FSM for purchase transactions.
Collects delivery details and initiates payment.
"""

from datetime import datetime

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.helpers.telegram import safe_answer_callback, safe_edit_text
from bot.helpers.residence import (
    build_floor_keyboard,
    build_hall_keyboard,
    build_room_keyboard,
    build_wing_keyboard,
    hall_from_index,
)
from bot.keyboards.main_menu import get_main_menu_inline
from core.config import get_settings
from db.models import CartItem, Listing, Order, OrderStatus, User
from services.logistics import add_business_days_excluding_sunday
from services.korapay import get_korapay_client

router = Router()
settings = get_settings()


class CheckoutStates(StatesGroup):
    awaiting_delivery_name = State()
    awaiting_delivery_hall = State()
    awaiting_delivery_wing = State()
    awaiting_delivery_floor = State()
    awaiting_delivery_room = State()
    awaiting_delivery_details = State()
    confirming_order = State()


def _resolve_customer_email(user: User, buyer_telegram_id: str) -> str:
    """
    Korapay requires a syntactically valid email.
    Telegram usernames are not emails, so never pass them directly.
    """
    if user.username and "@" in user.username:
        return user.username.strip().lower()
    return f"user_{buyer_telegram_id}@vendoor.app"


async def start_checkout(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    cart_item_ids = data.get("cart_item_ids") or []

    text: str
    if cart_item_ids:
        buyer_result = await session.execute(select(User).where(User.telegram_id == str(callback.from_user.id)))
        buyer = buyer_result.scalars().first()
        if not buyer:
            await safe_answer_callback(callback, text="Buyer account not found. Please send /start.", show_alert=True)
            return

        result = await session.execute(
            select(CartItem)
            .options(selectinload(CartItem.listing))
            .where(CartItem.id.in_(cart_item_ids))
            .where(CartItem.buyer_id == buyer.id)
            .order_by(CartItem.created_at.asc())
        )
        cart_items = result.scalars().all()
        if not cart_items:
            await safe_answer_callback(callback, text="Your cart is empty.", show_alert=True)
            return

        total_amount = sum((item.listing.buyer_price * item.quantity) for item in cart_items if item.listing)
        seller_count = len({item.listing.seller_id for item in cart_items if item.listing})
        processing_days = 2 if seller_count > 1 else 1

        text = (
            "<b>Cart Checkout</b>\n\n"
            f"Items: {len(cart_items)}\n"
            f"Sellers: {seller_count}\n"
            f"Estimated Processing: {processing_days} business day(s) (Sunday excluded)\n"
            f"Total: NGN {total_amount:,.2f}\n\n"
            "<b>Escrow Protection</b>\n"
            "Your payment is safe. Seller gets paid only after delivery confirmation window.\n\n"
            "Send your full delivery name to continue."
        )
        await state.update_data(checkout_mode="cart")
    else:
        listing_id = data.get("listing_id")
        result = await session.execute(select(Listing).where(Listing.id == listing_id))
        listing = result.scalars().first()

        if not listing:
            await safe_answer_callback(callback, text="Listing not found", show_alert=True)
            return
        if not listing.available or listing.quantity <= 0:
            await safe_answer_callback(callback, text="This item is out of stock", show_alert=True)
            return

        text = (
            f"<b>{listing.title}</b>\n\n"
            f"Price: NGN {listing.buyer_price:,.2f}\n"
            f"Quantity Left: {listing.quantity}\n"
            "(Includes 5% platform fee)\n\n"
            "<b>Escrow Protection</b>\n"
            "Your payment is safe. Seller gets paid only after you confirm receipt.\n\n"
            "Send your full delivery name to continue."
        )
        await state.update_data(checkout_mode="single")

    await safe_answer_callback(callback)
    if callback.message:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer(text, parse_mode="HTML")
    else:
        await callback.bot.send_message(chat_id=callback.from_user.id, text=text, parse_mode="HTML")
    await state.set_state(CheckoutStates.awaiting_delivery_name)


@router.message(CheckoutStates.awaiting_delivery_name)
async def handle_delivery_name(message: Message, state: FSMContext):
    full_name = (message.text or "").strip()
    if len(full_name) < 3:
        await message.reply("Please enter your full name (at least 3 characters).")
        return

    await state.update_data(delivery_contact_name=full_name)
    await message.answer(
        "<b>Delivery Location</b>\n\nSelect your hall:",
        parse_mode="HTML",
        reply_markup=build_hall_keyboard("checkout_hall_"),
    )
    await state.set_state(CheckoutStates.awaiting_delivery_hall)


@router.callback_query(
    F.data.startswith("checkout_hall_"),
    StateFilter(CheckoutStates.awaiting_delivery_hall),
)
async def handle_delivery_hall(callback: CallbackQuery, state: FSMContext):
    payload = (callback.data or "").replace("checkout_hall_", "", 1).strip()
    if not payload.isdigit():
        await safe_answer_callback(callback, text="Invalid hall selection.", show_alert=True)
        return

    hall = hall_from_index(int(payload))
    if not hall:
        await safe_answer_callback(callback, text="Invalid hall selection.", show_alert=True)
        return

    await safe_answer_callback(callback)
    await state.update_data(delivery_hall=hall)
    await safe_edit_text(
        callback,
        "<b>Delivery Location</b>\n\nSelect your wing (A-H):",
        parse_mode="HTML",
        reply_markup=build_wing_keyboard("checkout_wing_"),
    )
    await state.set_state(CheckoutStates.awaiting_delivery_wing)


@router.callback_query(
    F.data.startswith("checkout_wing_"),
    StateFilter(CheckoutStates.awaiting_delivery_wing),
)
async def handle_delivery_wing(callback: CallbackQuery, state: FSMContext):
    wing = (callback.data or "").replace("checkout_wing_", "", 1).strip().upper()
    if wing not in {"A", "B", "C", "D", "E", "F", "G", "H"}:
        await safe_answer_callback(callback, text="Invalid wing selection.", show_alert=True)
        return

    await safe_answer_callback(callback)
    await state.update_data(delivery_wing=wing)
    await safe_edit_text(
        callback,
        "<b>Delivery Location</b>\n\nSelect your floor:",
        parse_mode="HTML",
        reply_markup=build_floor_keyboard("checkout_floor_"),
    )
    await state.set_state(CheckoutStates.awaiting_delivery_floor)


@router.callback_query(
    F.data.startswith("checkout_floor_"),
    StateFilter(CheckoutStates.awaiting_delivery_floor),
)
async def handle_delivery_floor(callback: CallbackQuery, state: FSMContext):
    payload = (callback.data or "").replace("checkout_floor_", "", 1).strip()
    if not payload.isdigit():
        await safe_answer_callback(callback, text="Invalid floor selection.", show_alert=True)
        return
    floor = int(payload)
    if floor not in {1, 2, 3, 4}:
        await safe_answer_callback(callback, text="Invalid floor selection.", show_alert=True)
        return

    await safe_answer_callback(callback)
    await state.update_data(delivery_floor=floor)
    await safe_edit_text(
        callback,
        "<b>Delivery Location</b>\n\nSelect your room number:",
        parse_mode="HTML",
        reply_markup=build_room_keyboard("checkout_room_", floor),
    )
    await state.set_state(CheckoutStates.awaiting_delivery_room)


@router.callback_query(
    F.data.startswith("checkout_room_"),
    StateFilter(CheckoutStates.awaiting_delivery_room),
)
async def handle_delivery_room(callback: CallbackQuery, state: FSMContext):
    payload = (callback.data or "").replace("checkout_room_", "", 1).strip()
    if not payload.isdigit():
        await safe_answer_callback(callback, text="Invalid room selection.", show_alert=True)
        return

    room_number = int(payload)
    if room_number < 101 or room_number > 411:
        await safe_answer_callback(callback, text="Invalid room selection.", show_alert=True)
        return

    data = await state.get_data()
    floor = int(data.get("delivery_floor", 0))
    if room_number // 100 != floor:
        await safe_answer_callback(callback, text="Room does not match selected floor.", show_alert=True)
        return

    hall = str(data.get("delivery_hall") or "").strip()
    wing = str(data.get("delivery_wing") or "").strip().upper()
    full_name = str(data.get("delivery_contact_name") or "").strip()
    if not hall or wing not in {"A", "B", "C", "D", "E", "F", "G", "H"} or not full_name:
        await safe_answer_callback(callback, text="Delivery location session expired. Restart checkout.", show_alert=True)
        return

    await safe_answer_callback(callback)
    await state.update_data(
        delivery_room_number=room_number,
        delivery_address=(
            f"Name: {full_name}\n"
            f"Hall: {hall}\n"
            f"Wing: {wing}\n"
            f"Room: {room_number}"
        ),
    )
    await safe_edit_text(
        callback,
        "<b>Delivery Details</b>\n\n"
        "Any special instructions for delivery?\n"
        "(e.g., 'Leave at front desk', 'Call before arriving')\n\n"
        "Or type 'None' if there are no special instructions.",
        parse_mode="HTML",
    )
    await state.set_state(CheckoutStates.awaiting_delivery_details)


@router.message(CheckoutStates.awaiting_delivery_details)
async def handle_delivery_details(message: Message, state: FSMContext, session: AsyncSession):
    details = (message.text or "").strip()
    if not details:
        await message.reply("Please enter delivery details or type 'None'.")
        return
    if details.lower() == "none":
        details = None

    await state.update_data(delivery_details=details)
    data = await state.get_data()
    checkout_mode = data.get("checkout_mode")
    text: str
    if checkout_mode == "cart":
        buyer_result = await session.execute(select(User).where(User.telegram_id == str(message.from_user.id)))
        buyer = buyer_result.scalars().first()
        if not buyer:
            await message.answer("Buyer account not found. Please send /start and try again.")
            await state.clear()
            return

        cart_ids = data.get("cart_item_ids") or []
        result = await session.execute(
            select(CartItem)
            .options(selectinload(CartItem.listing))
            .where(CartItem.id.in_(cart_ids))
            .where(CartItem.buyer_id == buyer.id)
        )
        cart_items = result.scalars().all()
        if not cart_items:
            await message.answer("Your cart is empty now.")
            await state.clear()
            return

        seller_count = len({item.listing.seller_id for item in cart_items if item.listing})
        processing_days = 2 if seller_count > 1 else 1
        total_amount = sum((item.listing.buyer_price * item.quantity) for item in cart_items if item.listing)
        text = (
            "<b>Cart Order Confirmation</b>\n\n"
            f"<b>Items:</b> {len(cart_items)}\n"
            f"<b>Sellers:</b> {seller_count}\n"
            f"<b>Total:</b> NGN {total_amount:,.2f}\n"
            f"<b>Estimated Processing:</b> {processing_days} business day(s), excluding Sunday\n\n"
            f"<b>Delivery To:</b>\n{data.get('delivery_address')}\n\n"
        )
    else:
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
        "After delivery, please confirm receipt to complete the order."
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
    await safe_answer_callback(callback)
    checkout_mode = data.get("checkout_mode")

    try:
        result = await session.execute(select(User).where(User.telegram_id == buyer_telegram_id))
        buyer = result.scalars().first()
        if not buyer:
            await safe_edit_text(
                callback,
                "Buyer account not found. Please send /start and try again.",
                reply_markup=get_main_menu_inline(),
            )
            await state.clear()
            return

        korapay = get_korapay_client()
        if checkout_mode == "cart":
            cart_item_ids = data.get("cart_item_ids") or []
            result = await session.execute(
                select(CartItem)
                .options(selectinload(CartItem.listing))
                .where(CartItem.id.in_(cart_item_ids))
                .where(CartItem.buyer_id == buyer.id)
                .order_by(CartItem.created_at.asc())
            )
            cart_items = result.scalars().all()
            if not cart_items:
                await safe_edit_text(
                    callback,
                    "Your cart is empty now. Add items and try checkout again.",
                    reply_markup=get_main_menu_inline(),
                )
                await state.clear()
                return

            seller_count = len({item.listing.seller_id for item in cart_items if item.listing})
            processing_days = 2 if seller_count > 1 else 1
            eta = add_business_days_excluding_sunday(datetime.utcnow(), processing_days)

            created_orders: list[Order] = []
            for item in cart_items:
                listing = await session.get(Listing, item.listing_id, with_for_update=True)
                if not listing or not listing.available:
                    await session.rollback()
                    await safe_edit_text(
                        callback,
                        f"Stock changed for {item.listing.title if item.listing else 'an item'}. Please review cart.",
                        reply_markup=get_main_menu_inline(),
                    )
                    await state.clear()
                    return

                if listing.quantity < item.quantity:
                    await session.rollback()
                    await safe_edit_text(
                        callback,
                        f"Stock changed for {item.listing.title if item.listing else 'an item'}. Please review cart.",
                        reply_markup=get_main_menu_inline(),
                    )
                    await state.clear()
                    return

                order = Order(
                    buyer_id=buyer.id,
                    seller_id=listing.seller_id,
                    listing_id=listing.id,
                    quantity=item.quantity,
                    amount=listing.buyer_price * item.quantity,
                    status=OrderStatus.PENDING,
                    buyer_address=data.get("delivery_address"),
                    buyer_delivery_details=data.get("delivery_details"),
                    delivery_eta_at=eta,
                )
                session.add(order)
                await session.flush()
                created_orders.append(order)

            total_amount = sum(order.amount for order in created_orders)
            order_ids = [str(order.id) for order in created_orders]
            cart_reference = (
                f"VENDOOR_CART_{buyer_telegram_id}_{int(datetime.utcnow().timestamp())}_{'-'.join(order_ids)}"
            )
            korapay_ref = await korapay.initialize_charge(
                amount=total_amount,
                reference=cart_reference,
                customer_email=_resolve_customer_email(buyer, buyer_telegram_id),
                customer_name=buyer.first_name,
                callback_url=f"{settings.api_host}/webhooks/korapay",
            )
            if not korapay_ref:
                await session.rollback()
                await safe_edit_text(
                    callback,
                    "Payment initialization failed. Please retry checkout.",
                    reply_markup=get_main_menu_inline(),
                )
                await state.clear()
                return

            # Track reference on first order for easier audit trail without violating unique constraint.
            if created_orders:
                created_orders[0].transaction_ref = cart_reference[:255]

            for item in cart_items:
                await session.delete(item)
            await session.commit()

            order_count = len(created_orders)
            await safe_edit_text(
                callback,
                (
                    "<b>Cart Checkout Started</b>\n\n"
                    f"Created {order_count} order(s) across your cart.\n"
                    f"<b>Total Payment:</b> NGN {total_amount:,.2f}\n\n"
                    "Use the button below to pay once."
                ),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="Pay Cart Now", url=korapay_ref.checkout_url)],
                        [InlineKeyboardButton(text="Back to Menu", callback_data="back_to_menu")],
                    ]
                ),
            )
        else:
            listing_id = data.get("listing_id")
            result = await session.execute(select(Listing).where(Listing.id == listing_id).with_for_update())
            listing = result.scalars().first()
            if not listing or not listing.available:
                await safe_edit_text(
                    callback,
                    "This item is out of stock now. Please choose another listing.",
                    reply_markup=get_main_menu_inline(),
                )
                await state.clear()
                return
            if listing.quantity < 1:
                await safe_edit_text(
                    callback,
                    "This item is out of stock now. Please choose another listing.",
                    reply_markup=get_main_menu_inline(),
                )
                await state.clear()
                return

            pending_order_result = await session.execute(
                select(Order)
                .where(Order.buyer_id == buyer.id)
                .where(Order.listing_id == listing.id)
                .where(Order.status == OrderStatus.PENDING)
                .order_by(Order.created_at.desc())
                .limit(1)
            )
            existing_pending_order = pending_order_result.scalars().first()
            if existing_pending_order:
                await safe_edit_text(
                    callback,
                    (
                        "You already have a pending payment for this item.\n\n"
                        f"Pending Order ID: {existing_pending_order.id}\n"
                        "Please complete it first before creating another one."
                    ),
                    reply_markup=get_main_menu_inline(),
                )
                await state.clear()
                return

            order = Order(
                buyer_id=buyer.id,
                seller_id=listing.seller_id,
                listing_id=listing.id,
                quantity=1,
                amount=listing.buyer_price,
                status=OrderStatus.PENDING,
                buyer_address=data.get("delivery_address"),
                buyer_delivery_details=data.get("delivery_details"),
                delivery_eta_at=add_business_days_excluding_sunday(datetime.utcnow(), 1),
            )
            session.add(order)
            await session.flush()

            reference = f"VENDOOR_{order.id}_{buyer_telegram_id}"
            korapay_ref = await korapay.initialize_charge(
                amount=order.amount,
                reference=reference,
                customer_email=_resolve_customer_email(buyer, buyer_telegram_id),
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
