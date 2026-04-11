"""
Celery tasks for payout verification and reconciliation.
"""

import asyncio
import logging
from celery import shared_task

from db.session import create_engine, create_session_maker
from services.escrow import get_escrow_service

logger = logging.getLogger(__name__)


def _run_async(coro):
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(coro)


@shared_task
def verify_payout_by_reference(reference: str) -> dict:
    """
    Verify payout status against Korapay and persist the latest status.
    """
    return _run_async(_verify_payout_by_reference_async(reference))


async def _verify_payout_by_reference_async(reference: str) -> dict:
    try:
        engine = create_engine()
        session_maker = create_session_maker(engine)
        async with session_maker() as session:
            escrow = get_escrow_service()
            result = await escrow.verify_payout_by_reference(reference, session)
            return {"status": "success", **result}
    except Exception:
        logger.exception("Failed payout verification by reference=%s", reference)
        return {"status": "failed", "ok": False, "reference": reference, "error": "verification_exception"}
