"""
FastAPI application for VenDOOR Marketplace Bot.
Handles webhooks, API endpoints for payment callbacks, and administrative operations.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from db.session import init_db, close_db, get_session
from api.webhooks import korapay as korapay_webhook

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for FastAPI app.
    Initializes database on startup, closes on shutdown.
    """
    # Startup
    await init_db()
    yield
    # Shutdown
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
