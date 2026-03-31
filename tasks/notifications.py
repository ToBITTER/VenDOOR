"""
Notification tasks - send Telegram notifications to users.
"""

import asyncio
from celery import shared_task
import logging
from aiogram import Bot

from core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


@shared_task
def send_order_notification(user_telegram_id: str, message: str) -> dict:
    """
    Send notification to user about their order.
    This would integrate with the aiogram bot to send messages.
    """
    async def _send() -> None:
        bot = Bot(token=settings.telegram_bot_token)
        try:
            await bot.send_message(chat_id=int(user_telegram_id), text=message)
        finally:
            await bot.session.close()

    asyncio.run(_send())
    logger.info(f"Notification to {user_telegram_id}: {message}")
    return {"status": "sent", "user": user_telegram_id}


@shared_task
def send_pending_notifications() -> dict:
    """
    Periodic task to send pending notifications.
    Queries DB for notifications and sends them via bot.
    """
    # TODO: Implement periodic notification sending
    return {"status": "checked", "sent": 0}


@shared_task
def send_verification_email(email: str, verification_code: str) -> dict:
    """
    Send verification email to seller.
    """
    # TODO: Implement email sending (requires email service)
    logger.info(f"Verification email to {email}")
    return {"status": "sent", "email": email}
