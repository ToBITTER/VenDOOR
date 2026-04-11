"""
Celery configuration for VenDOOR.
Handles background tasks like escrow auto-release, email notifications, etc.
"""

import os
from celery import Celery
from celery.schedules import crontab

from core.config import get_settings

settings = get_settings()

# Create Celery app
app = Celery("vendoor")

# Configure broker and result backend
app.conf.update(
    broker_url=settings.celery_broker_url,
    result_backend=settings.celery_result_backend,
    timezone="UTC",
    enable_utc=True,
)

# Task configuration
app.conf.task_serializer = "json"
app.conf.accept_content = ["json"]
app.conf.result_serializer = "json"
app.conf.task_track_started = True
app.conf.task_time_limit = 30 * 60  # 30 minutes
app.conf.task_soft_time_limit = 25 * 60  # 25 minutes (soft limit before hard kill)

# Periodic task schedule (Celery Beat)
app.conf.beat_schedule = {
    "check-pending-escrows": {
        "task": "tasks.escrow_release.check_pending_escrows",
        "schedule": crontab(minute=0),  # Every hour at minute 0
    },
    "send-pending-notifications": {
        "task": "tasks.notifications.send_pending_notifications",
        "schedule": crontab(minute="*/15"),  # Every 15 minutes
    },
}

# Import tasks to register them
from tasks import escrow_release, notifications, payouts

__all__ = ["app"]
