"""
Buyer checkout FSM for purchase transactions.
Collects delivery details and initiates payment.
"""

from decimal import Decimal
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Order, OrderStatus, User, Listing
from bot.keyboards.main_menu import get_main_menu_inline
from services.korapay import get_korapay_client
from core.config import get_settings

router = Router()
settings = get_settings()


class CheckoutStates(StatesGroup):
    """FSM states for checkout."""
    awaiting_delivery_address = State()
    awaiting_delivery_details = State()
    confirming_order = State()


async def start_checkout(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    """
    Start checkout for the selected listing.
    """
    data = await state.get_data()
    listing_id = data.get("listing_id")
    
    # Get listing
    result = await session.execute(
        select(Listing).where(Listing.id == listing_id)
    )
    listing = result.scalars().first()
    
    if not listing:
        await callback.answer("❌ Listing not found", show_alert=True)
        return
    
    text = (
        f"📦 <b>{listing.title}</b>\n\n"
        f"Price: ₦{listing.buyer_price:,.2f}\n"
        f"(Includes 5% platform fee)\n\n"
        f"<b>🔒 Escrow Protection</b>\n"
        f"Your payment is safe. Seller gets paid only after you confirm receipt.\n\n"
        f"What's your delivery address?"
    )
    
    await callback.message.edit_text(text, parse_mode="HTML")
    await state.set_state(CheckoutStates.awaiting_delivery_address)
    await callback.answer()


@router.message(CheckoutStates.awaiting_delivery_address)
async def handle_delivery_address(message: Message, state: FSMContext):
    """Collect delivery address."""
    address = message.text.strip()
    
    if len(address) < 10:
        await message.reply("❌ Please provide a full delivery address.")
        return
    
    await state.update_data(delivery_address=address)
    
    await message.answer(
        "📅 <b>Delivery Details</b>\n\n"
        "Any special instructions for delivery?\n"
        "(e.g., 'Leave at front desk', 'Call before arriving', etc.)\n\n"
        "Or type 'None' if there are no special instructions.",
        parse_mode="HTML",
    )
    await state.set_state(CheckoutStates.awaiting_delivery_details)


@router.message(CheckoutStates.awaiting_delivery_details)
async def handle_delivery_details(message: Message, state: FSMContext, session: AsyncSession):
    """Collect delivery details and show order confirmation."""
    details = message.text.strip()
    if details.lower() == "none":
        details = None
    
    await state.update_data(delivery_details=details)
    data = await state.get_data()
    
    # Get listing and buyer info
    listing_id = data.get("listing_id")
    buyer_id = message.from_user.id
    
    result = await session.execute(
        select(Listing).where(Listing.id == listing_id)
    )
    listing = result.scalars().first()
    
    result = await session.execute(
        select(User).where(User.telegram_id == str(buyer_id))
    )
    buyer = result.scalars().first()
    
    # Show order confirmation
    text = (
        f"📋 <b>Order Confirmation</b>\n\n"
        f"<b>Product:</b> {listing.title}\n"
        f"<b>Price:</b> ₦{listing.buyer_price:,.2f}\n\n"
        f"<b>Delivery To:</b>\n{data.get('delivery_address')}\n\n"
    )
    
    if data.get("delivery_details"):
        text += f"<b>Special Instructions:</b>\n{data.get('delivery_details')}\n\n"
    
    text += (
        f"<b>🔒 Payment is protected by escrow</b>\n"
        f"Confirm receipt after receiving the item →\n"
        f"Money released to seller → Safe for both parties!"
    )
    
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Proceed to Payment", callback_data="proceed_payment")],
            [InlineKeyboardButton(text="❌ Cancel", callback_data="back_to_menu")],
        ]
    )
    
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)
    await state.set_state(CheckoutStates.confirming_order)


@router.callback_query(F.data == "proceed_payment", StateFilter(CheckoutStates.confirming_order))
async def proceed_to_payment(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    """
    Create order and redirect to Korapay payment.
    """
    data = await state.get_data()
    buyer_telegram_id = str(callback.from_user.id)
    listing_id = data.get("listing_id")
    
    try:
        # Get user and listing
        result = await session.execute(
            select(User).where(User.telegram_id == buyer_telegram_id)
        )
        buyer = result.scalars().first()
        
        result = await session.execute(
            select(Listing).where(Listing.id == listing_id)
        )
        listing = result.scalars().first()
        
        # Create order with PENDING status
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
        await session.flush()  # Get order.id without committing
        
        # Initialize Korapay charge
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
            # Save transaction reference and commit
            order.transaction_ref = reference
            await session.commit()
            
            # Send payment link
            text = (
                f"💳 <b>Payment Link</b>\n\n"
                f"Click the button below to complete payment of ₦{order.amount:,.2f}\n\n"
                f"Your order ID: {order.id}"
            )
            
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="💳 Pay Now", url=korapay_ref.checkout_url)],
                ]
            )
            
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
        else:
            await session.rollback()
            await callback.message.edit_text(
                "❌ Payment initialization failed. Please try again.",
                reply_markup=get_main_menu_inline(),
            )
        
    except Exception as e:
        await session.rollback()
        print(f"Checkout error: {e}")
        await callback.message.edit_text(
            f"❌ Error: {str(e)}",
            reply_markup=get_main_menu_inline(),
        )
    
    await state.clear()
    await callback.answer()
