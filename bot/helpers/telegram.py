"""
Telegram API safety helpers for callback UX and no-op edits.
"""

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery


def _is_not_modified_error(exc: TelegramBadRequest) -> bool:
    return "message is not modified" in str(exc).lower()


def _is_stale_callback_error(exc: TelegramBadRequest) -> bool:
    message = str(exc).lower()
    return "query is too old" in message or "query id is invalid" in message


async def safe_edit_text(callback: CallbackQuery, text: str, **kwargs) -> None:
    """
    Edit callback message text and ignore Telegram no-op update errors.
    """
    try:
        await callback.message.edit_text(text, **kwargs)
    except TelegramBadRequest as exc:
        if not _is_not_modified_error(exc):
            raise


async def safe_answer_callback(callback: CallbackQuery, **kwargs) -> bool:
    """
    Answer callback query and tolerate stale callback errors.
    Returns True when Telegram accepted the answer.
    """
    try:
        await callback.answer(**kwargs)
        return True
    except TelegramBadRequest as exc:
        if _is_stale_callback_error(exc):
            return False
        raise
