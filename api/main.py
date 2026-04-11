"""
FastAPI application for VenDOOR Marketplace Bot.
Handles webhooks, API endpoints for payment callbacks, and administrative operations.
"""

import asyncio
import hashlib
import logging
import secrets
import time
import uuid
import hmac
import json
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from typing import Literal
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram import Bot, Dispatcher
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Update
from aiogram.exceptions import TelegramRetryAfter
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from core.config import get_settings
from db.session import close_db, get_session
from api.webhooks import korapay as korapay_webhook
from bot.app import create_bot, create_dispatcher
from bot.main import set_default_commands

settings = get_settings()
logger = logging.getLogger(__name__)
UPDATE_SEMAPHORE = asyncio.Semaphore(20)
BROADCAST_SEMAPHORE = asyncio.Semaphore(20)
TELEGRAM_SECRET_HEADER = "x-telegram-bot-api-secret-token"


def _log_production_gaps() -> None:
    if settings.debug:
        return

    warnings: list[str] = []
    if not settings.admin_api_key:
        warnings.append("ADMIN_API_KEY is not set")
    if not settings.telegram_webhook_secret:
        warnings.append("TELEGRAM_WEBHOOK_SECRET is not set")
    if not settings.korapay_webhook_secret:
        warnings.append("KORAPAY_WEBHOOK_SECRET is not set")
    if any(host in {"*", "localhost", "127.0.0.1"} for host in settings.allowed_hosts_list):
        warnings.append("ALLOWED_HOSTS contains development/wildcard values")

    for warning in warnings:
        logger.warning("Production readiness warning: %s", warning)


class BroadcastRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4096)
    audience: Literal["all", "buyers", "sellers", "verified_sellers"] = "all"
    dry_run: bool = False


class VendorPrivilegeRequest(BaseModel):
    is_featured: bool | None = None
    priority_score: int | None = Field(default=None, ge=0, le=100)


class DeliveryAgentCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    vehicle_type: str | None = Field(default=None, max_length=100)


class DeliveryAgentUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    vehicle_type: str | None = Field(default=None, max_length=100)
    is_active: bool | None = None


class DeliveryAssignmentRequest(BaseModel):
    agent_id: int
    note: str | None = Field(default=None, max_length=500)


class DeliveryStatusUpdateRequest(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class DeliveryLocationUpdateRequest(BaseModel):
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    note: str | None = Field(default=None, max_length=255)


def require_admin(x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")) -> None:
    """
    Lightweight admin gate for operational endpoints.
    """
    if not settings.admin_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin API key is not configured",
        )
    if x_admin_key != settings.admin_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin credentials",
        )


async def require_delivery_agent(
    x_delivery_key: str | None = Header(default=None, alias="X-Delivery-Key"),
    session: AsyncSession = Depends(get_session),
):
    if not x_delivery_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing delivery key")

    from db.models import DeliveryAgent

    api_key_hash = hashlib.sha256(x_delivery_key.encode("utf-8")).hexdigest()
    result = await session.execute(
        select(DeliveryAgent).where(DeliveryAgent.api_key_hash == api_key_hash)
    )
    agent = result.scalars().first()
    if not agent or not agent.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid delivery credentials")
    return agent


async def _configure_bot(bot: Bot) -> None:
    """
    Configure bot commands and webhook without blocking API startup.
    """
    webhook_url = settings.bot_webhook_url or f"{settings.api_host.rstrip('/')}/webhooks/telegram"
    try:
        await set_default_commands(bot)
        await bot.set_webhook(
            webhook_url,
            drop_pending_updates=True,
            secret_token=settings.telegram_webhook_secret,
        )
        logger.info("Telegram webhook configured: %s", webhook_url)
        
        # Initialize notification service with bot instance
        from services.delivery_notifications import set_bot_instance
        set_bot_instance(bot)
    except Exception:
        logger.exception("Failed to configure Telegram webhook/commands at startup")


async def _run_update(dispatcher: Dispatcher, bot: Bot, update: Update) -> None:
    async with UPDATE_SEMAPHORE:
        try:
            await dispatcher.feed_update(bot, update)
        except Exception:
            logger.exception("Unhandled exception while processing Telegram update")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for FastAPI app.
    Closes database resources on shutdown.
    """
    app.state.bot = create_bot()
    app.state.dispatcher = create_dispatcher()
    app.state.bot_setup_task = asyncio.create_task(_configure_bot(app.state.bot))
    _log_production_gaps()

    yield
    # Shutdown
    bot_setup_task = getattr(app.state, "bot_setup_task", None)
    if bot_setup_task and not bot_setup_task.done():
        bot_setup_task.cancel()
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
    allow_origins=settings.cors_allow_origins,
    allow_credentials="*" not in settings.cors_allow_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    """
    Attach a request ID for traceability and prevent uncaught errors from crashing request handling.
    """
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    request.state.request_id = request_id
    start_time = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "Unhandled API exception request_id=%s method=%s path=%s",
            request_id,
            request.method,
            request.url.path,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error", "request_id": request_id},
            headers={"X-Request-ID": request_id},
        )

    duration_ms = (time.perf_counter() - start_time) * 1000
    if duration_ms > 2000:
        logger.warning(
            "Slow request request_id=%s method=%s path=%s duration_ms=%.1f",
            request_id,
            request.method,
            request.url.path,
            duration_ms,
        )
    response.headers["X-Request-ID"] = request_id
    return response


# ============================================================================
# Health Check Endpoint
# ============================================================================

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "service": "vendoor-api"}


@app.get("/")
async def root():
    """Root endpoint for platform probes."""
    return {"status": "ok", "service": "vendoor-api", "health": "/health"}


# ============================================================================
# Webhook Routes
# ============================================================================

@app.post("/webhooks/korapay")
async def korapay_webhook_handler(request: Request, session: AsyncSession = Depends(get_session)):
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
    try:
        raw_body = await request.body()
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception:
        logger.exception("Invalid Korapay webhook payload")
        raise HTTPException(status_code=400, detail="Invalid webhook payload")

    if settings.korapay_webhook_secret or settings.korapay_secret_key:
        signature = request.headers.get("x-korapay-signature")
        if not signature:
            raise HTTPException(status_code=401, detail="Missing Korapay signature")
        normalized_signature = signature.strip()
        if normalized_signature.lower().startswith("sha256="):
            normalized_signature = normalized_signature.split("=", 1)[1].strip()

        # Korapay signs ONLY the "data" object with HMAC SHA256 using your secret key.
        # Different providers may preserve key order / spacing differently, so we validate
        # against a few safe serializations of the same object.
        signed_data = payload.get("data", {})
        serialized_candidates = [
            json.dumps(signed_data, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
            json.dumps(signed_data, ensure_ascii=False).encode("utf-8"),
            json.dumps(signed_data, separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode("utf-8"),
            raw_body,
        ]

        candidate_keys = []
        if settings.korapay_webhook_secret:
            candidate_keys.append(settings.korapay_webhook_secret)
        # Per Korapay docs, secret key is used for signing.
        candidate_keys.append(settings.korapay_secret_key)

        is_valid = False
        for key in candidate_keys:
            for serialized_data in serialized_candidates:
                computed = hmac.new(key.encode("utf-8"), serialized_data, hashlib.sha256).hexdigest()
                if hmac.compare_digest(computed, normalized_signature):
                    is_valid = True
                    break
            if is_valid:
                break

        if not is_valid:
            raise HTTPException(status_code=401, detail="Invalid Korapay signature")

    return await korapay_webhook.handle_korapay_webhook(payload, session, app.state.bot)


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
    if settings.telegram_webhook_secret:
        received_secret = request.headers.get(TELEGRAM_SECRET_HEADER)
        if not received_secret or not hmac.compare_digest(
            received_secret,
            settings.telegram_webhook_secret,
        ):
            logger.warning("Rejected Telegram webhook: invalid secret token")
            raise HTTPException(status_code=401, detail="Invalid Telegram webhook token")

    try:
        update_data = await request.json()
        update = Update.model_validate(update_data)
    except Exception:
        logger.exception("Invalid Telegram webhook payload")
        return {"ok": False, "error": "invalid_payload"}

    # Process update in background so webhook responds quickly to Telegram.
    asyncio.create_task(_run_update(dispatcher, bot, update))

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
    from db.models import Order
    
    order = await session.get(Order, order_id)
    
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    return {
        "order_id": order.id,
        "status": order.status.value,
        "quantity": order.quantity,
        "amount": str(order.amount),
        "created_at": order.created_at.isoformat(),
    }


# ============================================================================
# Admin Endpoints (Protected in production)
# ============================================================================

@app.get("/admin/stats")
async def admin_stats(
    _: None = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """
    Get marketplace statistics (orders, sellers, etc).
    Should be protected with authentication in production.
    """
    from db.models import Listing, Order, OrderStatus, SellerProfile

    total_orders_sq = select(func.count(Order.id)).scalar_subquery()
    completed_orders_sq = (
        select(func.count(Order.id)).where(Order.status == OrderStatus.COMPLETED).scalar_subquery()
    )
    verified_sellers_sq = (
        select(func.count(SellerProfile.id)).where(SellerProfile.verified.is_(True)).scalar_subquery()
    )
    active_listings_sq = (
        select(func.count(Listing.id)).where(Listing.available.is_(True)).scalar_subquery()
    )

    result = await session.execute(
        select(
            total_orders_sq.label("total_orders"),
            completed_orders_sq.label("completed_orders"),
            verified_sellers_sq.label("verified_sellers"),
            active_listings_sq.label("active_listings"),
        )
    )
    stats = result.one()
    
    return {
        "total_orders": stats.total_orders or 0,
        "completed_orders": stats.completed_orders or 0,
        "verified_sellers": stats.verified_sellers or 0,
        "active_listings": stats.active_listings or 0,
    }


@app.get("/admin/vendors")
async def list_all_vendors(
    _: None = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """
    List all seller profiles (vendors) with verification and account metadata.
    """
    from db.models import Listing, Order, SellerProfile, User

    total_result = await session.execute(select(func.count(SellerProfile.id)))
    total = total_result.scalar() or 0

    listings_count_sq = (
        select(Listing.seller_id.label("seller_id"), func.count(Listing.id).label("listings_count"))
        .group_by(Listing.seller_id)
        .subquery()
    )
    tx_count_sq = (
        select(Order.seller_id.label("seller_id"), func.count(Order.id).label("transactions_count"))
        .group_by(Order.seller_id)
        .subquery()
    )

    result = await session.execute(
        select(
            SellerProfile,
            User,
            func.coalesce(listings_count_sq.c.listings_count, 0).label("listings_count"),
            func.coalesce(tx_count_sq.c.transactions_count, 0).label("transactions_count"),
        )
        .join(User, SellerProfile.user_id == User.id)
        .outerjoin(listings_count_sq, listings_count_sq.c.seller_id == SellerProfile.id)
        .outerjoin(tx_count_sq, tx_count_sq.c.seller_id == SellerProfile.id)
        .order_by(SellerProfile.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    rows = result.all()

    vendor_rows = []
    for seller, user, listings_count, transactions_count in rows:
        vendor_rows.append(
            {
                "seller_id": seller.id,
                "seller_code": seller.seller_code,
                "user_id": seller.user_id,
                "telegram_id": user.telegram_id,
                "name": f"{user.first_name} {user.last_name or ''}".strip(),
                "username": user.username,
                "verified": seller.verified,
                "is_featured": seller.is_featured,
                "priority_score": seller.priority_score,
                "full_name": seller.full_name,
                "level": seller.level,
                "is_student": seller.is_student,
                "student_email": seller.student_email,
                "hall": seller.hall,
                "room_number": seller.room_number,
                "address": seller.address,
                "bank_code": seller.bank_code,
                "account_number": seller.account_number,
                "account_name": seller.account_name,
                "listings_count": listings_count,
                "transactions_count": transactions_count,
                "created_at": seller.created_at.isoformat(),
            }
        )

    return {"total": total, "limit": limit, "offset": offset, "vendors": vendor_rows}


@app.post("/admin/vendors/{seller_id}/privileges")
async def update_vendor_privileges(
    seller_id: int,
    payload: VendorPrivilegeRequest,
    _: None = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """
    Update vendor privilege settings (featured and ranking priority).
    """
    from db.models import SellerProfile

    seller = await session.get(SellerProfile, seller_id)
    if not seller:
        raise HTTPException(status_code=404, detail="Vendor not found")

    if payload.is_featured is None and payload.priority_score is None:
        raise HTTPException(status_code=400, detail="No privilege fields supplied")

    if payload.is_featured is not None:
        seller.is_featured = payload.is_featured
    if payload.priority_score is not None:
        seller.priority_score = payload.priority_score

    await session.commit()
    return {
        "ok": True,
        "seller_id": seller.id,
        "seller_code": seller.seller_code,
        "is_featured": seller.is_featured,
        "priority_score": seller.priority_score,
    }


@app.delete("/admin/vendors/{seller_id}")
async def delete_vendor(
    seller_id: int,
    _: None = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """
    Delete a vendor profile and its listings when it has no transaction history.
    """
    from db.models import Order, SellerProfile

    seller = await session.get(SellerProfile, seller_id)
    if not seller:
        raise HTTPException(status_code=404, detail="Vendor not found")

    order_count_result = await session.execute(
        select(func.count(Order.id)).where(Order.seller_id == seller_id)
    )
    order_count = order_count_result.scalar() or 0
    if order_count > 0:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete vendor with existing transactions",
        )

    await session.delete(seller)
    await session.commit()
    return {"ok": True, "deleted_vendor_id": seller_id, "deleted_vendor_code": seller.seller_code}


@app.delete("/admin/users/{user_id}")
async def delete_user(
    user_id: int,
    _: None = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """
    Delete a user only when they have no buyer/seller transactions.
    """
    from db.models import Order, SellerProfile, User

    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    buyer_order_count_result = await session.execute(
        select(func.count(Order.id)).where(Order.buyer_id == user_id)
    )
    buyer_order_count = buyer_order_count_result.scalar() or 0
    if buyer_order_count > 0:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete user with buyer transactions",
        )

    seller_result = await session.execute(select(SellerProfile.id).where(SellerProfile.user_id == user_id))
    seller_id = seller_result.scalar_one_or_none()
    if seller_id is not None:
        seller_order_count_result = await session.execute(
            select(func.count(Order.id)).where(Order.seller_id == seller_id)
        )
        seller_order_count = seller_order_count_result.scalar() or 0
        if seller_order_count > 0:
            raise HTTPException(
                status_code=409,
                detail="Cannot delete user with seller transactions",
            )

    await session.delete(user)
    await session.commit()
    return {"ok": True, "deleted_user_id": user_id}


async def _send_message_with_retry(bot: Bot, chat_id: int, text: str) -> tuple[bool, str | None]:
    async with BROADCAST_SEMAPHORE:
        try:
            await bot.send_message(chat_id=chat_id, text=text)
            return True, None
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after)
            try:
                await bot.send_message(chat_id=chat_id, text=text)
                return True, None
            except Exception as retry_exc:
                return False, str(retry_exc)
        except Exception as exc:
            return False, str(exc)


def _serialize_delivery(delivery, include_order: bool = True) -> dict:
    payload = {
        "delivery_id": delivery.id,
        "order_id": delivery.order_id,
        "status": delivery.status.value,
        "agent_id": delivery.agent_id,
        "assigned_at": delivery.assigned_at.isoformat() if delivery.assigned_at else None,
        "picked_up_at": delivery.picked_up_at.isoformat() if delivery.picked_up_at else None,
        "in_transit_at": delivery.in_transit_at.isoformat() if delivery.in_transit_at else None,
        "delivered_at": delivery.delivered_at.isoformat() if delivery.delivered_at else None,
        "current_latitude": float(delivery.current_latitude) if delivery.current_latitude is not None else None,
        "current_longitude": float(delivery.current_longitude) if delivery.current_longitude is not None else None,
        "current_location_note": delivery.current_location_note,
        "created_at": delivery.created_at.isoformat(),
        "updated_at": delivery.updated_at.isoformat(),
    }
    if include_order and delivery.order:
        payload["order"] = {
            "buyer_id": delivery.order.buyer_id,
            "seller_id": delivery.order.seller_id,
            "amount": str(delivery.order.amount),
            "order_status": delivery.order.status.value,
            "delivery_eta_at": (
                delivery.order.delivery_eta_at.isoformat() if delivery.order.delivery_eta_at else None
            ),
            "delivery_confirm_deadline_at": (
                delivery.order.delivery_confirm_deadline_at.isoformat()
                if delivery.order.delivery_confirm_deadline_at
                else None
            ),
        }
    return payload


def _generate_delivery_api_key() -> tuple[str, str]:
    raw_key = f"vdl_{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    return raw_key, key_hash


@app.post("/admin/broadcast")
async def broadcast_message(
    payload: BroadcastRequest,
    _: None = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """
    Broadcast a message to users.
    Supports audience targeting and dry-run mode.
    """
    from db.models import Order, SellerProfile, User

    if payload.audience == "all":
        recipients_query = select(User.telegram_id).where(User.telegram_id.is_not(None)).distinct()
    elif payload.audience == "buyers":
        recipients_query = (
            select(User.telegram_id)
            .join(Order, Order.buyer_id == User.id)
            .where(User.telegram_id.is_not(None))
            .distinct()
        )
    elif payload.audience == "sellers":
        recipients_query = (
            select(User.telegram_id)
            .join(SellerProfile, SellerProfile.user_id == User.id)
            .where(User.telegram_id.is_not(None))
            .distinct()
        )
    else:  # verified_sellers
        recipients_query = (
            select(User.telegram_id)
            .join(SellerProfile, SellerProfile.user_id == User.id)
            .where(SellerProfile.verified.is_(True))
            .where(User.telegram_id.is_not(None))
            .distinct()
        )

    recipient_result = await session.execute(recipients_query)
    recipient_ids = [row[0] for row in recipient_result.all() if row[0]]
    total_recipients = len(recipient_ids)

    if payload.dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "audience": payload.audience,
            "recipient_count": total_recipients,
        }

    bot: Bot = app.state.bot
    sent = 0
    failed = 0
    failures: list[dict[str, str]] = []

    for telegram_id in recipient_ids:
        try:
            chat_id = int(telegram_id)
        except Exception:
            failed += 1
            failures.append({"telegram_id": str(telegram_id), "error": "invalid_telegram_id"})
            continue

        ok, error = await _send_message_with_retry(bot, chat_id, payload.message)
        if ok:
            sent += 1
        else:
            failed += 1
            failures.append({"telegram_id": str(telegram_id), "error": error or "send_failed"})

    return {
        "ok": True,
        "dry_run": False,
        "audience": payload.audience,
        "recipient_count": total_recipients,
        "sent": sent,
        "failed": failed,
        "failures": failures[:100],
    }


@app.get("/admin/transactions")
async def list_all_transactions(
    _: None = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """
    List all orders/transactions with current status.
    """
    from db.models import Order, SellerProfile

    total_result = await session.execute(select(func.count(Order.id)))
    total = total_result.scalar() or 0

    result = await session.execute(
        select(Order)
        .options(
            joinedload(Order.buyer),
            joinedload(Order.seller).joinedload(SellerProfile.user),
            joinedload(Order.listing),
            joinedload(Order.delivery),
        )
        .order_by(Order.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    orders = result.scalars().all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "transactions": [
            {
                "order_id": order.id,
                "status": order.status.value,
                "quantity": order.quantity,
                "amount": str(order.amount),
                "transaction_ref": order.transaction_ref,
                "seller_payout_ref": order.seller_payout_ref,
                "seller_payout_status": order.seller_payout_status,
                "seller_payout_attempted_at": (
                    order.seller_payout_attempted_at.isoformat() if order.seller_payout_attempted_at else None
                ),
                "buyer": {
                    "user_id": order.buyer.id if order.buyer else None,
                    "telegram_id": order.buyer.telegram_id if order.buyer else None,
                    "name": order.buyer.first_name if order.buyer else None,
                },
                "seller": {
                    "seller_id": order.seller.id if order.seller else None,
                    "seller_code": order.seller.seller_code if order.seller else None,
                    "user_id": order.seller.user.id if order.seller and order.seller.user else None,
                    "name": order.seller.user.first_name
                    if order.seller and order.seller.user
                    else None,
                },
                "listing": {
                    "listing_id": order.listing.id if order.listing else None,
                    "listing_code": order.listing.listing_code if order.listing else None,
                    "title": order.listing.title if order.listing else None,
                },
                "buyer_address": order.buyer_address,
                "delivery_eta_at": order.delivery_eta_at.isoformat() if order.delivery_eta_at else None,
                "delivered_at": order.delivered_at.isoformat() if order.delivered_at else None,
                "delivery_confirm_deadline_at": (
                    order.delivery_confirm_deadline_at.isoformat()
                    if order.delivery_confirm_deadline_at
                    else None
                ),
                "created_at": order.created_at.isoformat(),
                "updated_at": order.updated_at.isoformat(),
            }
            for order in orders
        ],
    }


@app.post("/admin/payouts/{reference}/verify")
async def verify_payout_reference(
    reference: str,
    _: None = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """
    Verify a payout by reference and persist latest status on related order.
    """
    from services.escrow import get_escrow_service

    escrow = get_escrow_service()
    result = await escrow.verify_payout_by_reference(reference, session)
    if not result.get("ok") and result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@app.post("/admin/payouts/{reference}/verify-async")
async def verify_payout_reference_async(
    reference: str,
    _: None = Depends(require_admin),
):
    """
    Queue payout verification task by reference.
    """
    from tasks.payouts import verify_payout_by_reference as verify_task

    task = verify_task.delay(reference)
    return {"queued": True, "task_id": task.id, "reference": reference}


@app.get("/admin/listings")
async def list_all_listings(
    _: None = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """
    List all product listings with availability status.
    """
    from db.models import Listing, SellerProfile

    total_result = await session.execute(select(func.count(Listing.id)))
    total = total_result.scalar() or 0

    result = await session.execute(
        select(Listing)
        .options(joinedload(Listing.seller).joinedload(SellerProfile.user))
        .order_by(Listing.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    listings = result.scalars().all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "listings": [
            {
                "listing_id": listing.id,
                "listing_code": listing.listing_code,
                "title": listing.title,
                "description": listing.description,
                "category": listing.category.value,
                "accessory_subcategory": (
                    listing.accessory_subcategory.value if listing.accessory_subcategory else None
                ),
                "base_price": str(listing.base_price),
                "buyer_price": str(listing.buyer_price),
                "quantity": listing.quantity,
                "available": listing.available,
                "image_url": listing.image_url,
                "seller": {
                    "seller_id": listing.seller.id if listing.seller else None,
                    "seller_code": listing.seller.seller_code if listing.seller else None,
                    "verified": listing.seller.verified if listing.seller else None,
                    "is_featured": listing.seller.is_featured if listing.seller else None,
                    "priority_score": listing.seller.priority_score if listing.seller else None,
                    "name": listing.seller.user.first_name
                    if listing.seller and listing.seller.user
                    else None,
                },
                "created_at": listing.created_at.isoformat(),
            }
            for listing in listings
        ],
    }


@app.delete("/admin/listings/{listing_id}")
async def delete_listing(
    listing_id: int,
    _: None = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """
    Delete a listing from admin panel.
    For safety, listing cannot be deleted if linked to transactions.
    """
    from db.models import Listing, Order

    listing = await session.get(Listing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    order_count_result = await session.execute(
        select(func.count(Order.id)).where(Order.listing_id == listing_id)
    )
    order_count = order_count_result.scalar() or 0
    if order_count > 0:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete listing with existing transactions",
        )

    await session.delete(listing)
    await session.commit()
    return {
        "ok": True,
        "deleted_listing_id": listing_id,
        "deleted_listing_code": listing.listing_code,
    }


@app.get("/admin/sellers/pending")
async def list_pending_seller_verifications(
    _: None = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """
    List sellers waiting for manual admin verification.
    """
    from db.models import SellerProfile, User

    result = await session.execute(
        select(SellerProfile)
        .options(joinedload(SellerProfile.user))
        .where(SellerProfile.verified.is_(False))
        .order_by(SellerProfile.created_at.asc())
    )
    sellers = result.scalars().all()

    return {
        "count": len(sellers),
        "pending_sellers": [
            {
                "seller_id": seller.id,
                "seller_code": seller.seller_code,
                "user_id": seller.user_id,
                "telegram_id": seller.user.telegram_id if seller.user else None,
                "name": f"{seller.user.first_name} {seller.user.last_name or ''}".strip()
                if seller.user
                else None,
                "username": seller.user.username if seller.user else None,
                "is_student": seller.is_student,
                "is_featured": seller.is_featured,
                "priority_score": seller.priority_score,
                "full_name": seller.full_name,
                "level": seller.level,
                "student_email": seller.student_email,
                "hall": seller.hall,
                "room_number": seller.room_number,
                "address": seller.address,
                "id_document_url": seller.id_document_url,
                "bank_code": seller.bank_code,
                "account_number": seller.account_number,
                "account_name": seller.account_name,
                "created_at": seller.created_at.isoformat(),
            }
            for seller in sellers
        ],
    }


@app.post("/admin/sellers/{seller_id}/approve")
async def approve_seller_verification(
    seller_id: int,
    _: None = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """
    Approve a pending seller and notify them in Telegram.
    """
    from db.models import SellerProfile

    result = await session.execute(
        select(SellerProfile).options(joinedload(SellerProfile.user)).where(SellerProfile.id == seller_id)
    )
    seller = result.scalars().first()
    if not seller:
        raise HTTPException(status_code=404, detail="Seller not found")

    seller.verified = True
    await session.commit()

    if seller.user and seller.user.telegram_id:
        try:
            await app.state.bot.send_message(
                chat_id=int(seller.user.telegram_id),
                text=(
                    "Your seller account has been verified.\n\n"
                    "You can now create listings and start selling on VenDOOR."
                ),
            )
        except Exception:
            logger.exception("Failed to notify seller %s after approval", seller.id)

    return {
        "ok": True,
        "seller_id": seller.id,
        "seller_code": seller.seller_code,
        "verified": seller.verified,
    }


@app.post("/admin/sellers/{seller_id}/reject")
async def reject_seller_verification(
    seller_id: int,
    reason: str | None = None,
    _: None = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """
    Reject a pending seller application and notify them.
    Rejection keeps profile but sets verified=False.
    """
    from db.models import SellerProfile

    result = await session.execute(
        select(SellerProfile).options(joinedload(SellerProfile.user)).where(SellerProfile.id == seller_id)
    )
    seller = result.scalars().first()
    if not seller:
        raise HTTPException(status_code=404, detail="Seller not found")

    seller.verified = False
    await session.commit()

    if seller.user and seller.user.telegram_id:
        try:
            message = "Your seller verification was not approved yet."
            if reason:
                message += f"\n\nReason: {reason}"
            message += "\n\nPlease update your details and try again."
            await app.state.bot.send_message(chat_id=int(seller.user.telegram_id), text=message)
        except Exception:
            logger.exception("Failed to notify seller %s after rejection", seller.id)

    return {
        "ok": True,
        "seller_id": seller.id,
        "seller_code": seller.seller_code,
        "verified": seller.verified,
        "reason": reason,
    }


@app.post("/admin/delivery-agents")
async def create_delivery_agent(
    payload: DeliveryAgentCreateRequest,
    _: None = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    from db.models import DeliveryAgent

    raw_key, key_hash = _generate_delivery_api_key()
    agent = DeliveryAgent(
        name=payload.name.strip(),
        phone=payload.phone.strip() if payload.phone else None,
        vehicle_type=payload.vehicle_type.strip() if payload.vehicle_type else None,
        api_key_hash=key_hash,
        is_active=True,
    )
    session.add(agent)
    await session.commit()
    await session.refresh(agent)
    return {
        "ok": True,
        "agent": {
            "id": agent.id,
            "name": agent.name,
            "phone": agent.phone,
            "vehicle_type": agent.vehicle_type,
            "is_active": agent.is_active,
            "created_at": agent.created_at.isoformat(),
        },
        "api_key": raw_key,
    }


@app.get("/admin/delivery-agents")
async def list_delivery_agents(
    _: None = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    from db.models import DeliveryAgent

    result = await session.execute(select(DeliveryAgent).order_by(DeliveryAgent.created_at.desc()))
    agents = result.scalars().all()
    return {
        "count": len(agents),
        "agents": [
            {
                "id": agent.id,
                "name": agent.name,
                "phone": agent.phone,
                "vehicle_type": agent.vehicle_type,
                "is_active": agent.is_active,
                "created_at": agent.created_at.isoformat(),
                "updated_at": agent.updated_at.isoformat(),
            }
            for agent in agents
        ],
    }


@app.patch("/admin/delivery-agents/{agent_id}")
async def update_delivery_agent(
    agent_id: int,
    payload: DeliveryAgentUpdateRequest,
    _: None = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    from db.models import DeliveryAgent

    agent = await session.get(DeliveryAgent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Delivery agent not found")

    if payload.name is not None:
        agent.name = payload.name.strip()
    if payload.phone is not None:
        agent.phone = payload.phone.strip() or None
    if payload.vehicle_type is not None:
        agent.vehicle_type = payload.vehicle_type.strip() or None
    if payload.is_active is not None:
        agent.is_active = payload.is_active

    await session.commit()
    return {
        "ok": True,
        "agent": {
            "id": agent.id,
            "name": agent.name,
            "phone": agent.phone,
            "vehicle_type": agent.vehicle_type,
            "is_active": agent.is_active,
            "updated_at": agent.updated_at.isoformat(),
        },
    }


@app.post("/admin/deliveries/{delivery_id}/assign-agent")
async def assign_delivery_agent(
    delivery_id: int,
    payload: DeliveryAssignmentRequest,
    _: None = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    from db.models import Delivery, DeliveryAgent, DeliveryEvent, DeliveryEventType, DeliveryStatus, Order, User

    delivery_result = await session.execute(
        select(Delivery)
        .options(joinedload(Delivery.order).joinedload(Order.buyer))
        .where(Delivery.id == delivery_id)
    )
    delivery = delivery_result.scalars().first()
    if not delivery:
        raise HTTPException(status_code=404, detail="Delivery not found")

    agent = await session.get(DeliveryAgent, payload.agent_id)
    if not agent or not agent.is_active:
        raise HTTPException(status_code=400, detail="Delivery agent unavailable")

    delivery.agent_id = agent.id
    delivery.status = DeliveryStatus.ASSIGNED
    delivery.assigned_at = datetime.utcnow()
    session.add(
        DeliveryEvent(
            delivery_id=delivery.id,
            event_type=DeliveryEventType.ASSIGNED,
            actor="ADMIN",
            note=payload.note or f"Assigned to {agent.name}",
        )
    )
    await session.commit()

    buyer = delivery.order.buyer if delivery.order else None
    if buyer and buyer.telegram_id:
        try:
            track_keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="Track Delivery",
                            callback_data=f"order_track_{delivery.order_id}",
                        )
                    ]
                ]
            )
            await app.state.bot.send_message(
                chat_id=int(buyer.telegram_id),
                text=(
                    "Your order has been assigned to an agent.\n\n"
                    f"Order: #{delivery.order_id}\n"
                    f"Agent: {agent.name}\n"
                    f"Phone: {agent.phone or 'N/A'}\n\n"
                    "Tap the button below to track updates."
                ),
                reply_markup=track_keyboard,
            )
        except Exception:
            logger.exception("Failed to notify buyer for assigned delivery %s", delivery.id)

    # Notify agent via Telegram with pickup details
    if agent.telegram_id:
        from services.delivery_notifications import notify_agent_delivery_assigned
        try:
            await notify_agent_delivery_assigned(delivery.id, session)
        except Exception:
            logger.exception("Failed to notify agent for assigned delivery %s", delivery.id)

    return {"ok": True, "delivery": _serialize_delivery(delivery)}


@app.get("/admin/deliveries")
async def admin_list_deliveries(
    status_filter: str | None = Query(default=None, alias="status"),
    _: None = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    from db.models import Delivery, DeliveryStatus, Order

    query = select(Delivery).options(joinedload(Delivery.order).joinedload(Order.buyer)).order_by(
        Delivery.created_at.desc()
    )
    if status_filter:
        try:
            status_value = DeliveryStatus[status_filter.upper()]
        except KeyError:
            raise HTTPException(status_code=400, detail="Invalid delivery status filter")
        query = query.where(Delivery.status == status_value)
    result = await session.execute(query)
    deliveries = result.scalars().all()
    return {"count": len(deliveries), "deliveries": [_serialize_delivery(d) for d in deliveries]}


@app.get("/delivery-agent/jobs")
async def delivery_agent_jobs(
    agent=Depends(require_delivery_agent),
    session: AsyncSession = Depends(get_session),
):
    from db.models import Delivery, DeliveryStatus, Order

    result = await session.execute(
        select(Delivery)
        .options(joinedload(Delivery.order).joinedload(Order.buyer))
        .where(Delivery.agent_id == agent.id)
        .where(Delivery.status.in_([DeliveryStatus.ASSIGNED, DeliveryStatus.PICKED_UP, DeliveryStatus.IN_TRANSIT]))
        .order_by(Delivery.updated_at.desc())
    )
    deliveries = result.scalars().all()
    return {"count": len(deliveries), "jobs": [_serialize_delivery(d) for d in deliveries]}


@app.post("/delivery-agent/jobs/{delivery_id}/pickup")
async def delivery_pickup(
    delivery_id: int,
    payload: DeliveryStatusUpdateRequest,
    agent=Depends(require_delivery_agent),
    session: AsyncSession = Depends(get_session),
):
    from db.models import Delivery, DeliveryEvent, DeliveryEventType, DeliveryStatus

    delivery = await session.get(Delivery, delivery_id)
    if not delivery or delivery.agent_id != agent.id:
        raise HTTPException(status_code=404, detail="Delivery job not found")

    delivery.status = DeliveryStatus.PICKED_UP
    delivery.picked_up_at = datetime.utcnow()
    session.add(
        DeliveryEvent(
            delivery_id=delivery.id,
            event_type=DeliveryEventType.PICKED_UP,
            actor="AGENT",
            note=payload.note,
        )
    )
    await session.commit()
    return {"ok": True, "delivery": _serialize_delivery(delivery, include_order=False)}


@app.post("/delivery-agent/jobs/{delivery_id}/in-transit")
async def delivery_in_transit(
    delivery_id: int,
    payload: DeliveryStatusUpdateRequest,
    agent=Depends(require_delivery_agent),
    session: AsyncSession = Depends(get_session),
):
    from db.models import Delivery, DeliveryEvent, DeliveryEventType, DeliveryStatus

    delivery = await session.get(Delivery, delivery_id)
    if not delivery or delivery.agent_id != agent.id:
        raise HTTPException(status_code=404, detail="Delivery job not found")

    delivery.status = DeliveryStatus.IN_TRANSIT
    delivery.in_transit_at = datetime.utcnow()
    session.add(
        DeliveryEvent(
            delivery_id=delivery.id,
            event_type=DeliveryEventType.IN_TRANSIT,
            actor="AGENT",
            note=payload.note,
        )
    )
    await session.commit()
    return {"ok": True, "delivery": _serialize_delivery(delivery, include_order=False)}


@app.post("/delivery-agent/jobs/{delivery_id}/location")
async def delivery_location_update(
    delivery_id: int,
    payload: DeliveryLocationUpdateRequest,
    agent=Depends(require_delivery_agent),
    session: AsyncSession = Depends(get_session),
):
    from db.models import Delivery, DeliveryEvent, DeliveryEventType

    delivery = await session.get(Delivery, delivery_id)
    if not delivery or delivery.agent_id != agent.id:
        raise HTTPException(status_code=404, detail="Delivery job not found")

    delivery.current_latitude = payload.latitude
    delivery.current_longitude = payload.longitude
    delivery.current_location_note = payload.note
    session.add(
        DeliveryEvent(
            delivery_id=delivery.id,
            event_type=DeliveryEventType.LOCATION_UPDATE,
            actor="AGENT",
            note=payload.note,
            latitude=payload.latitude,
            longitude=payload.longitude,
        )
    )
    await session.commit()
    return {"ok": True, "delivery": _serialize_delivery(delivery, include_order=False)}


@app.post("/delivery-agent/jobs/{delivery_id}/delivered")
async def delivery_delivered(
    delivery_id: int,
    payload: DeliveryStatusUpdateRequest,
    agent=Depends(require_delivery_agent),
    session: AsyncSession = Depends(get_session),
):
    from db.models import Delivery, DeliveryEvent, DeliveryEventType, DeliveryStatus, Order, User

    delivery_result = await session.execute(
        select(Delivery)
        .options(joinedload(Delivery.order).joinedload(Order.buyer))
        .where(Delivery.id == delivery_id)
    )
    delivery = delivery_result.scalars().first()
    if not delivery or delivery.agent_id != agent.id:
        raise HTTPException(status_code=404, detail="Delivery job not found")

    now = datetime.utcnow()
    confirmation_deadline = now + timedelta(hours=settings.escrow_release_hours)

    delivery.status = DeliveryStatus.DELIVERED
    delivery.delivered_at = now
    if delivery.order:
        delivery.order.delivered_at = now
        delivery.order.delivery_confirm_deadline_at = confirmation_deadline
        delivery.order.auto_release_scheduled_at = confirmation_deadline

    session.add(
        DeliveryEvent(
            delivery_id=delivery.id,
            event_type=DeliveryEventType.DELIVERED,
            actor="AGENT",
            note=payload.note,
        )
    )
    await session.commit()

    buyer = delivery.order.buyer if delivery.order else None
    if buyer and buyer.telegram_id:
        try:
            await app.state.bot.send_message(
                chat_id=int(buyer.telegram_id),
                text=(
                    f"Order #{delivery.order_id} has been marked delivered.\n\n"
                    "Please confirm receipt to release payment."
                ),
            )
        except Exception:
            logger.exception("Failed to notify buyer for delivered order %s", delivery.order_id)

    return {"ok": True, "delivery": _serialize_delivery(delivery)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
    )
