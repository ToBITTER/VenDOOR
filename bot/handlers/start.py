"""
Start handler for /start command.
Displays welcome message and main menu.
"""

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command, CommandStart
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.helpers.telegram import safe_answer_callback, safe_edit_text
from db.models import User
from bot.keyboards.main_menu import get_main_menu_inline

# Router for start command
router = Router()


@router.message(CommandStart())
async def start_handler(message: Message, session: AsyncSession):
    """
    Handle /start command.
    Create user if doesn't exist, then show main menu.
    """
    telegram_id = str(message.from_user.id)
    
    # Check if user exists
    result = await session.execute(
        select(User).where(User.telegram_id == telegram_id)
    )
    user = result.scalars().first()
    
    if not user:
        # Create new user
        user = User(
            telegram_id=telegram_id,
            username=message.from_user.username,
            first_name=message.from_user.first_name or "User",
            last_name=message.from_user.last_name,
        )
        session.add(user)
        await session.commit()
    
    # Send welcome message
    welcome_text = (
        "🎉 <b>Welcome to VenDOOR Marketplace!</b>\n\n"
        "Your campus marketplace for buying and selling.\n\n"
        "💰 Secure transactions with escrow protection\n"
        "✅ Verified sellers\n"
        "🚀 Fast delivery\n\n"
        "What would you like to do?"
    )
    
    await message.answer(
        welcome_text,
        parse_mode="HTML",
        reply_markup=get_main_menu_inline(),
    )


@router.callback_query(F.data == "back_to_menu")
async def back_to_menu_handler(callback: CallbackQuery):
    """
    Handle back to menu button.
    """
    welcome_text = (
        "🎉 <b>Welcome to VenDOOR Marketplace!</b>\n\n"
        "What would you like to do?"
    )
    
    await safe_answer_callback(callback)
    await safe_edit_text(callback, welcome_text, parse_mode="HTML", reply_markup=get_main_menu_inline())


@router.callback_query(F.data == "help")
async def help_handler(callback: CallbackQuery):
    """
    Handle help button.
    """
    help_text = (
        "❓ <b>VenDOOR Help</b>\n\n"
        "<b>Buying:</b>\n"
        "1. Browse catalog by category\n"
        "2. Select an item and pay\n"
        "3. Funds go to escrow\n"
        "4. Confirm receipt when you get the item\n\n"
        
        "<b>Selling:</b>\n"
        "1. Register as a seller\n"
        "2. Verify your identity (student or non-student)\n"
        "3. Create listings\n"
        "4. Get paid when buyers confirm receipt\n\n"
        
        "<b>Escrow:</b>\n"
        "Your payment is protected! Seller doesn't get paid until you confirm receipt.\n"
        "After 48 hours with no dispute, funds auto-release to seller.\n\n"
        
        "<b>Issues?</b>\n"
        "Raise a dispute from your orders.\n"
    )
    
    await safe_answer_callback(callback)
    await safe_edit_text(callback, help_text, parse_mode="HTML", reply_markup=get_main_menu_inline())
