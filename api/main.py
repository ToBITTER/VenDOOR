"""
FastAPI application for VenDOOR Marketplace Bot.
Handles webhooks, API endpoints for payment callbacks, and administrative operations.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram import Bot, Dispatcher
from aiogram.types import Update
from aiogram.exceptions import TelegramBadRequest

from core.config import get_settings
from db.session import close_db, get_session
from api.webhooks import korapay as korapay_webhook
from bot.app import create_bot, create_dispatcher
from bot.main import set_default_commands

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for FastAPI app.
    Closes database resources on shutdown.
    """
    app.state.bot = create_bot()
    app.state.dispatcher = create_dispatcher()

    webhook_url = settings.bot_webhook_url or f"{settings.api_host.rstrip('/')}/webhooks/telegram"

    await set_default_commands(app.state.bot)
    await app.state.bot.set_webhook(webhook_url, drop_pending_updates=True)

    yield
    # Shutdown
    await app.state.bot.session.close()
    await close_db()


# Create FastAPI app instance
app = FastAPI(
    title="VenDOOR Marketplace API",
    description="Payment gateway and webhook endpoints for VenDOOR Telegram bot",
    version="1.0.0",
    lifespan=lifespan,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_hosts_list if settings.allowed_hosts_list else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# Health Check Endpoint
# ============================================================================

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "service": "vendoor-api"}


# ============================================================================
# Webhook Routes
# ============================================================================

@app.post("/webhooks/korapay")
async def korapay_webhook_handler(request: dict, session: AsyncSession = Depends(get_session)):
    """
    Korapay payment gateway webhook handler.
    Called by Korapay when payment status changes.
    
    Example payload:
    {
        "event": "charge.completed",
        "data": {
            "ref": "transaction_reference",
            "status": "success",
            "amount": 5000.00,
            ...
        }
    }
    """
    return await korapay_webhook.handle_korapay_webhook(request, session)


@app.get("/webhooks/telegram")
@app.get("/webhooks/telegram/")
async def telegram_webhook_probe():
    """
    Probe endpoint for quick webhook path verification in browser/log checks.
    """
    return {"ok": True, "path": "/webhooks/telegram"}


@app.post("/webhooks/telegram")
@app.post("/webhooks/telegram/")
async def telegram_webhook_handler(request: Request):
    """
    Telegram webhook endpoint.
    Receives Telegram updates and feeds them into aiogram dispatcher.
    """
    bot: Bot = app.state.bot
    dispatcher: Dispatcher = app.state.dispatcher

    update_data = await request.json()
    update = Update.model_validate(update_data)
    try:
        await dispatcher.feed_update(bot, update)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise

    return {"ok": True}


# ============================================================================
# Order Status Endpoint
# ============================================================================

@app.get("/orders/{order_id}/status")
async def get_order_status(order_id: int, session: AsyncSession = Depends(get_session)):
    """
    Get order status by order ID.
    Used by bot to check payment status after timeout.
    """
    from sqlalchemy import select
    from db.models import Order
    
    result = await session.execute(select(Order).where(Order.id == order_id))
    order = result.scalars().first()
    
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    return {
        "order_id": order.id,
        "status": order.status.value,
        "amount": str(order.amount),
        "created_at": order.created_at.isoformat(),
    }


# ============================================================================
# Admin Endpoints (Protected in production)
# ============================================================================

@app.get("/admin/stats")
async def admin_stats(session: AsyncSession = Depends(get_session)):
    """
    Get marketplace statistics (orders, sellers, etc).
    Should be protected with authentication in production.
    """
    from sqlalchemy import func
    from db.models import Order, OrderStatus, SellerProfile, Listing
    
    # Total orders
    result = await session.execute(func.count(Order.id).select())
    total_orders = result.scalar() or 0
    
    # Completed orders
    result = await session.execute(
        func.count(Order.id).select().where(Order.status == OrderStatus.COMPLETED)
    )
    completed_orders = result.scalar() or 0
    
    # Verified sellers
    result = await session.execute(
        func.count(SellerProfile.id).select().where(SellerProfile.verified == True)
    )
    verified_sellers = result.scalar() or 0
    
    # Active listings
    result = await session.execute(
        func.count(Listing.id).select().where(Listing.available == True)
    )
    active_listings = result.scalar() or 0
    
    return {
        "total_orders": total_orders,
        "completed_orders": completed_orders,
        "verified_sellers": verified_sellers,
        "active_listings": active_listings,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
    )
