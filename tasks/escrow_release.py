"""
Celery task for automatically releasing escrow funds after 48 hours.
Triggered by Korapay webhook when payment completes.
"""

from datetime import datetime
from celery import shared_task
from sqlalchemy import select

from db.models import Order, OrderStatus
from db.session import create_session_maker, create_engine
from services.escrow import get_escrow_service


@shared_task(bind=True)
async def release_escrow_auto(self, order_id: int) -> dict:
    """
    Automatically release escrow funds to seller after 48 hours if not disputed.
    
    This task is scheduled by the Korapay webhook when payment completes.
    It should only run if no dispute has been raised.
    
    Args:
        order_id: Order ID to release escrow for
    
    Returns:
        Dictionary with status and message
    """
    try:
        # Create async session
        engine = create_engine()
        session_maker = create_session_maker(engine)
        
        async with session_maker() as session:
            # Check if order still exists and is in escrow (PAID status)
            result = await session.execute(
                select(Order).where(Order.id == order_id)
            )
            order = result.scalars().first()
            
            if not order:
                return {
                    "status": "failed",
                    "message": f"Order {order_id} not found",
                }
            
            # Don't auto-release if disputed
            if order.status == OrderStatus.DISPUTED:
                return {
                    "status": "cancelled",
                    "message": f"Order {order_id} is disputed, escrow not auto-released",
                }
            
            # Only auto-release if in PAID status
            if order.status != OrderStatus.PAID:
                return {
                    "status": "skipped",
                    "message": f"Order {order_id} status is {order.status}, not PAID",
                }
            
            # Release escrow
            escrow_service = get_escrow_service()
            success = await escrow_service.auto_release_escrow(order_id, session)
            
            if success:
                return {
                    "status": "success",
                    "message": f"Escrow released for order {order_id}",
                    "order_id": order_id,
                }
            else:
                return {
                    "status": "failed",
                    "message": f"Failed to release escrow for order {order_id}",
                }
    
    except Exception as e:
        logger.error(f"Error in release_escrow_auto task: {e}")
        # Retry task up to 3 times
        raise self.retry(exc=e, countdown=300, max_retries=3)


@shared_task
def check_pending_escrows() -> dict:
    """
    Periodic task to check for escrows that should be auto-released.
    Can be scheduled with Celery beat (e.g., every 1 hour).
    
    For production, consider using Celery Beat:
    @periodic_task(run_every=crontab(minute=0))  # Every hour
    """
    # This would query all PAID orders where auto_release_scheduled_at <= now()
    # and call release_escrow_auto for each
    pass


# Celery configuration
# In celery.py or settings:
# CELERY_BEAT_SCHEDULE = {
#     'check-escrows-every-hour': {
#         'task': 'tasks.escrow_release.check_pending_escrows',
#         'schedule': crontab(minute=0),
#     },
# }


import logging
logger = logging.getLogger(__name__)
