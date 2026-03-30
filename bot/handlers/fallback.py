"""
Fallback handlers for unmatched updates.
"""

from aiogram import Router
from aiogram.types import CallbackQuery, Message

from bot.helpers.telegram import safe_answer_callback, safe_edit_text
from bot.keyboards.main_menu import get_main_menu_inline

router = Router()


@router.message()
async def fallback_message_handler(message: Message):
    text = (
        "I did not understand that message.\n\n"
        "Use the menu below or send /start to open the main menu."
    )
    await message.answer(text, reply_markup=get_main_menu_inline())


@router.callback_query()
async def fallback_callback_handler(callback: CallbackQuery):
    await safe_answer_callback(callback)
    await safe_edit_text(
        callback,
        "That button is no longer active. Please use the menu below.",
        reply_markup=get_main_menu_inline(),
    )
