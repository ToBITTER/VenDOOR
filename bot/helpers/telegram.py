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


def _is_no_text_to_edit_error(exc: TelegramBadRequest) -> bool:
    return "there is no text in the message to edit" in str(exc).lower()


def _is_no_caption_to_edit_error(exc: TelegramBadRequest) -> bool:
    return "there is no caption in the message to edit" in str(exc).lower()


async def safe_edit_text(callback: CallbackQuery, text: str, **kwargs) -> None:
    """
    Edit callback message text and ignore Telegram no-op update errors.
    """
    if not callback.message:
        return

    try:
        await callback.message.edit_text(text, **kwargs)
    except TelegramBadRequest as exc:
        if _is_not_modified_error(exc):
            return

        if _is_no_text_to_edit_error(exc):
            try:
                await callback.message.edit_caption(caption=text, **kwargs)
                return
            except TelegramBadRequest as caption_exc:
                if _is_not_modified_error(caption_exc):
                    return
                if _is_no_caption_to_edit_error(caption_exc):
                    await callback.message.answer(text, **kwargs)
                    return
                raise

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


async def safe_render_text_screen(callback: CallbackQuery, text: str, **kwargs) -> None:
    """
    Render a clean text screen for navigation actions.
    If current callback message is a photo, delete it and send a new text message.
    """
    if not callback.message:
        return

    if callback.message.photo:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer(text, **kwargs)
        return

    await safe_edit_text(callback, text, **kwargs)
