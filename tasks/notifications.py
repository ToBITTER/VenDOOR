"""
Notification tasks - send Telegram notifications to users.
"""

from celery import shared_task
import logging

logger = logging.getLogger(__name__)


@shared_task
def send_order_notification(user_telegram_id: str, message: str) -> dict:
    """
    Send notification to user about their order.
    This would integrate with the aiogram bot to send messages.
    """
    # TODO: Implement bot.send_message(user_telegram_id, message)
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
