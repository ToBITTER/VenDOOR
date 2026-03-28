# VenDOOR Development Guide

## Project Summary

VenDOOR is a complete asynchronous Telegram marketplace bot with:
- **Dual FSM flows**: Seller registration + Buyer checkout
- **Payment gateway**: Korapay integration with webhooks
- **Escrow system**: Automatic fund protection & release
- **Async-first**: aiogram 3.0, FastAPI, SQLAlchemy 2.0
- **Production ready**: Docker, Celery, migrations, error handling

---

## Architecture Overview

```
User (Telegram)
    ↓
Bot (aiogram) ← → Database (PostgreSQL)
    ↓
API (FastAPI)
    ↓
Korapay Gateway ← → Payment Webhook
    ↓
Celery Tasks (Redis)
```

---

## File Structure

### Core System
- `core/config.py` — Configuration management
- `db/models.py` — 5 SQLAlchemy models (User, Seller, Listing, Order, Complaint)
- `db/session.py` — Async engine & session factory
- `alembic/` — Database migrations

### Bot Handlers (FSMs)
- `bot/handlers/start.py` — Welcome & main menu
- `bot/handlers/seller/register.py` — **Seller registration FSM** (student/non-student)
- `bot/handlers/seller/listings.py` — **Create & manage listings FSM**
- `bot/handlers/buyer/catalog.py` — Category browsing
- `bot/handlers/buyer/checkout.py` — **Checkout FSM** (address → payment)
- `bot/handlers/buyer/orders.py` — View orders, confirm receipt
- `bot/handlers/complaints.py` — **Dispute filing FSM**

### Services & API
- `services/korapay.py` — Payment client (initialize, verify)
- `services/escrow.py` — Escrow state machine
- `api/main.py` — FastAPI app with admin routes
- `api/webhooks/korapay.py` — Payment webhook handler

### Background Tasks
- `celery.py` — Celery configuration & beat schedule
- `tasks/escrow_release.py` — Auto-release escrow (48h)
- `tasks/notifications.py` — Send Telegram notifications

### UI Components
- `bot/keyboards/main_menu.py` — All inline keyboards
- `bot/middlewares/db.py` — Database session injection

---

## FSM Flows

### 1. Seller Registration (`seller/register.py`)
```
START
  ↓ "Become Seller"
Student? (Y/N)
  ├─ YES → Student Email → ID Document → Bank Code → Account Number → Account Name
  └─ NO  → ID Document → Bank Code → Account Number → Account Name
  ↓
Confirm Details
  ↓
Save to DB + Pending Verification
END
```

### 2. Listing Creation (`seller/listings.py`)
```
START
  ↓ "Create Listing"
Title → Description → Category → Base Price
  ↓
Confirm & Create
  ↓
"Go live!"
END
```

### 3. Buyer Checkout (`buyer/checkout.py`)
```
START
  ↓ Browse Catalog
Select Product
  ↓ "Buy Now"
Delivery Address → Delivery Instructions
  ↓
Confirm Order
  ↓
Initialize Korapay Charge
  ↓
"Click link to pay"
  ↓
Pay on Korapay
  ↓
Webhook updates order → PAID
  ↓
"Confirm receipt" (after 48h auto-release)
END
```

### 4. File Complaint (`complaints.py`)
```
START
  ↓ "Raise Dispute"
Select Order
  ↓
Subject → Description → Evidence (photo)
  ↓
Confirm & Submit
  ↓
Admin Reviews
  ↓
Resolution Sent
END
```

---

## Key Features Implemented

✅ **Database**
- 5 models with relationships, enums, indexes
- Alembic migrations (auto + manual)
- Async SQLAlchemy 2.0

✅ **Bot**
- Dispatcher with middleware injection
- 4 major FSMs (register, listing, checkout, complaint)
- Inline keyboards with callbacks
- Session persistence

✅ **Payments**
- Korapay API client (httpx)
- Webhook handler for notifications
- Transaction reference tracking

✅ **Escrow**
- Order state machine (PENDING → PAID → COMPLETED)
- 48-hour auto-release
- Dispute blocking

✅ **Admin**
- Stats endpoint (`/admin/stats`)
- Order lookup (`/orders/{id}/status`)
- Webhook handler logs

✅ **DevOps**
- Docker Compose (Postgres + Redis)
- Celery with Redis broker
- Alembic migrations
- .env configuration

---

## Quick Commands

### Database
```bash
# Create migration after changing models
alembic revision --autogenerate -m "Description"
alembic upgrade head  # Apply migrations
alembic downgrade -1  # Rollback
```

### Running Services
```bash
# Bot
python bot/main.py

# API
uvicorn api.main:app --reload  # http://localhost:8000

# Celery Worker
celery -A celery worker -l info

# Celery Beat (for scheduled tasks)
celery -A celery beat -l info

# Docker services
docker-compose up -d      # Start
docker-compose down       # Stop
docker-compose logs -f    # View logs
```

### Database Access
```bash
# PostgreSQL CLI
psql -h localhost -U vendoor_user -d vendoor

# View Redis
redis-cli
> KEYS *
> DEL key
```

---

## Testing Payment Flow

1. Create a test user (runs /start)
2. Register as seller
3. Create a listing
4. Switch account, browse catalog
5. Initiate checkout → Copy Korapay link
6. Mock webhook: `curl -X POST http://localhost:8000/webhooks/korapay -d {...}`
7. Check order status in DB

---

## Environment Variables

See `ENV_GUIDE.md` for complete list. Key ones:

```env
TELEGRAM_BOT_TOKEN=xxx              # From @BotFather
DATABASE_URL=postgresql+asyncpg://... # Docker default works
REDIS_URL=redis://localhost:6379/0
KORAPAY_PUBLIC_KEY=xxx
KORAPAY_SECRET_KEY=xxx
```

---

## Production Checklist

- [ ] Set `DEBUG=False`
- [ ] Use managed PostgreSQL (RDS, Heroku, etc.)
- [ ] Use managed Redis (Redis Cloud, etc.)
- [ ] Update `KORAPAY_BASE_URL` & credentials
- [ ] Set up HTTPS for webhook URL
- [ ] Configure logging & monitoring
- [ ] Add rate limiting to API
- [ ] Implement email notifications
- [ ] Set up database backups
- [ ] Monitor Celery tasks
- [ ] Use `gunicorn` instead of `uvicorn reload`

---

## Extending VenDOOR

### Add new handler
```python
# bot/handlers/my_feature.py
from aiogram import Router
router = Router()

@router.message(...)
async def my_handler(message):
    ...

# bot/main.py
dispatcher.include_router(my_handler.router)
```

### Add new model
```python
# db/models.py - add class
class MyModel(Base):
    ...

# alembic
alembic revision --autogenerate -m "Add MyModel"
alembic upgrade head
```

### Add new API endpoint
```python
@app.get("/my-endpoint")
async def my_endpoint(session: AsyncSession = Depends(get_session)):
    ...
```

---

## Troubleshooting

**Bot not receiving messages:**
- Check `TELEGRAM_BOT_TOKEN` in .env
- Confirm bot is running: `python bot/main.py`
- Restart bot (polling may timeout)

**Payment webhook not firing:**
- Ensure API is running: `uvicorn api.main:app`
- Check Korapay dashboard for webhook logs
- Update `BOT_WEBHOOK_URL` in .env if using HTTPS

**Migrations failing:**
- Check if PostgreSQL is running: `docker-compose logs postgres`
- Verify `DATABASE_URL` in .env
- Check migration syntax in `alembic/versions/`

**Celery tasks not running:**
- Ensure Redis is running: `redis-cli ping`
- Start worker: `celery -A celery worker -l info`
- Check task imports in `celery.py`

---

## Support & Contribution

For issues contact the team. PRs welcome!

---

**Last Updated**: 28 March 2026  
**Status**: Production Ready ✅
