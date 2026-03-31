"""
Logistics helpers for delivery ETA and receipt confirmation windows.
"""

from datetime import datetime, timedelta


def add_business_days_excluding_sunday(start_at: datetime, days: int) -> datetime:
    """
    Add business days while excluding Sunday only.
    """
    if days <= 0:
        return start_at

    cursor = start_at
    added = 0
    while added < days:
        cursor = cursor + timedelta(days=1)
        if cursor.weekday() != 6:  # Sunday
            added += 1
    return cursor
