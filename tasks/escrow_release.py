"""
Celery task for automatically releasing escrow funds after 48 hours.
Triggered by Korapay webhook when payment completes.
"""

import asyncio
import logging
from datetime import datetime
from celery import shared_task
from sqlalchemy import select

from db.models import Order, OrderStatus
from db.session import create_session_maker, create_engine
from services.escrow import get_escrow_service

logger = logging.getLogger(__name__)


def _run_async(coro):
    """Helper to run async code in Celery task."""
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(coro)


@shared_task(bind=True)
def release_escrow_auto(self, order_id: int) -> dict:
    """
    Automatically release escrow funds to seller after 48 hours if not disputed.
    
    This task is scheduled by the Korapay webhook when payment completes.
    It should only run if no dispute has been raised.
    
    Args:
        order_id: Order ID to release escrow for
    
    Returns:
        Dictionary with status and message
    """
    return _run_async(_release_escrow_async(order_id))


async def _release_escrow_async(order_id: int) -> dict:
    """Async implementation of escrow release."""
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
        # Retry task up to 3 times (will use exponential backoff)
        raise Exception(f"Task failed: {e}")


@shared_task
def check_pending_escrows() -> dict:
    """
    Periodic task to check for escrows that should be auto-released.
    Runs every hour via Celery Beat.
    """
    return _run_async(_check_pending_escrows_async())


async def _check_pending_escrows_async() -> dict:
    """Async implementation of checking pending escrows."""
    try:
        engine = create_engine()
        session_maker = create_session_maker(engine)
        
        async with session_maker() as session:
            # Find all PAID orders where auto_release_scheduled_at is in the past
            result = await session.execute(
                select(Order).where(
                    (Order.status == OrderStatus.PAID) &
                    (Order.auto_release_scheduled_at <= datetime.utcnow())
                )
            )
            orders = result.scalars().all()
            
            released_count = 0
            for order in orders:
                try:
                    escrow_service = get_escrow_service()
                    success = await escrow_service.auto_release_escrow(order.id, session)
                    if success:
                        released_count += 1
                except Exception as e:
                    logger.error(f"Failed to auto-release order {order.id}: {e}")
                    continue
            
            return {
                "status": "success",
                "checked": len(orders),
                "released": released_count,
            }
    
    except Exception as e:
        logger.error(f"Error in check_pending_escrows task: {e}")
        return {
            "status": "failed",
            "message": str(e),
        }
