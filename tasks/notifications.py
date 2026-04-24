"""
Notification tasks - send Telegram notifications to users.
"""

import asyncio
from datetime import datetime, timedelta
from celery import shared_task
import logging
from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from core.config import get_settings
from db.models import NotificationLog, Order, OrderStatus
from db.session import create_engine, create_session_maker

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

    try:
        asyncio.run(_send())
        logger.info("Notification sent to %s", user_telegram_id)
        return {"status": "sent", "user": user_telegram_id}
    except Exception:
        logger.exception("Failed to send direct notification to %s", user_telegram_id)
        return {"status": "failed", "user": user_telegram_id}


@shared_task
def send_pending_notifications() -> dict:
    """
    Periodic task to send pending notifications.
    Queries DB for notifications and sends them via bot.
    """
    async def _run() -> dict:
        now = datetime.utcnow()
        # Task runs every 15 minutes; notify buyers whose auto-release deadline is near.
        window_start = now
        window_end = now + timedelta(minutes=20)

        engine = create_engine()
        session_maker = create_session_maker(engine)
        bot = Bot(token=settings.telegram_bot_token)
        checked = 0
        sent = 0
        skipped = 0

        try:
            async with session_maker() as session:
                result = await session.execute(
                    select(Order)
                    .options(joinedload(Order.buyer))
                    .where(Order.status == OrderStatus.PAID)
                    .where(Order.delivered_at.is_not(None))
                    .where(Order.delivery_confirm_deadline_at.is_not(None))
                    .where(Order.delivery_confirm_deadline_at >= window_start)
                    .where(Order.delivery_confirm_deadline_at <= window_end)
                )
                orders = result.scalars().all()
                checked = len(orders)

                for order in orders:
                    buyer = order.buyer
                    if not buyer or not buyer.telegram_id or not str(buyer.telegram_id).isdigit():
                        skipped += 1
                        continue

                    dedupe_key = (
                        f"delivery_confirmation_reminder:{order.id}:"
                        f"{order.delivery_confirm_deadline_at.strftime('%Y%m%d%H%M') if order.delivery_confirm_deadline_at else 'na'}"
                    )
                    already_sent_result = await session.execute(
                        select(NotificationLog.id)
                        .where(NotificationLog.dedupe_key == dedupe_key)
                        .limit(1)
                    )
                    if already_sent_result.scalar_one_or_none():
                        skipped += 1
                        continue

                    try:
                        await bot.send_message(
                            chat_id=int(buyer.telegram_id),
                            text=(
                                "<b>Delivery Confirmation Reminder</b>\n\n"
                                f"Order #{order.id} has been marked delivered.\n"
                                "Please confirm receipt from My Orders if everything is okay.\n\n"
                                "If there is an issue, raise a dispute immediately."
                            ),
                            parse_mode="HTML",
                        )
                        session.add(
                            NotificationLog(
                                user_id=buyer.id,
                                channel="telegram",
                                event_type="delivery_confirmation_reminder",
                                status="sent",
                                dedupe_key=dedupe_key,
                                context_ref=f"order:{order.id}",
                                message="Reminder sent before auto-release deadline",
                                sent_at=datetime.utcnow(),
                            )
                        )
                        sent += 1
                    except Exception:
                        skipped += 1
                        session.add(
                            NotificationLog(
                                user_id=buyer.id,
                                channel="telegram",
                                event_type="delivery_confirmation_reminder",
                                status="failed",
                                dedupe_key=f"{dedupe_key}:failed:{int(datetime.utcnow().timestamp())}",
                                context_ref=f"order:{order.id}",
                                message="Reminder send failed",
                            )
                        )
                        logger.exception("Failed sending reminder for order %s", order.id)
                await session.commit()
        finally:
            await bot.session.close()
            await engine.dispose()

        return {"status": "checked", "checked": checked, "sent": sent, "skipped": skipped}

    try:
        return asyncio.run(_run())
    except Exception:
        logger.exception("send_pending_notifications task failed")
        return {"status": "failed", "sent": 0}


@shared_task
def send_verification_email(email: str, verification_code: str) -> dict:
    """
    Send verification email to seller.
    """
    logger.warning(
        "Email service not configured; verification email not sent to %s (code length=%s)",
        email,
        len(str(verification_code or "")),
    )
    return {"status": "skipped_not_configured", "email": email}
