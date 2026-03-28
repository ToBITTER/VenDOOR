"""
Complete environment variables guide for VenDOOR.
Copy content to .env file and fill in your values.
"""

# ============================================================================
# TELEGRAM BOT
# ============================================================================

# Get your bot token from @BotFather on Telegram
TELEGRAM_BOT_TOKEN=123456789:ABCDefghIjklmnoPqrstuvwxyz-AbCDEfg

# ============================================================================
# DATABASE (PostgreSQL)
# ============================================================================

# If using Docker: postgresql+asyncpg://vendoor_user:vendoor_password@localhost:5432/vendoor
# For production, use a managed service (AWS RDS, Heroku Postgres, etc.)
DATABASE_URL=postgresql+asyncpg://vendoor_user:vendoor_password@localhost:5432/vendoor

# Echo SQL queries (set to False in production)
DATABASE_ECHO=False

# ============================================================================
# REDIS
# ============================================================================

# For caching and sessions
# If using Docker: redis://localhost:6379/0
# For production: redis-cloud or similar managed service
REDIS_URL=redis://localhost:6379/0

# ============================================================================
# KORAPAY PAYMENT GATEWAY
# ============================================================================

# Get these from your Korapay dashboard: https://dashboard.korapay.com
KORAPAY_PUBLIC_KEY=pk_live_xxxxxxxxxxxxx
KORAPAY_SECRET_KEY=sk_live_xxxxxxxxxxxxx
KORAPAY_BASE_URL=https://api.korapay.com/merchant/api/v1

# ============================================================================
# CELERY & MESSAGE BROKER
# ============================================================================

# Using Redis as broker
CELERY_BROKER_URL=redis://localhost:6379/1
CELERY_RESULT_BACKEND=redis://localhost:6379/2

# ============================================================================
# API SETTINGS
# ============================================================================

# Debug mode (set to False in production)
DEBUG=False

# Allowed hosts for CORS
ALLOWED_HOSTS=localhost,127.0.0.1,yourdomain.com

# API base URL (for Korapay callback)
API_HOST=http://localhost:8000

# Webhook URL for Korapay to call your API
# Example: https://yourdomain.com/webhooks/korapay
BOT_WEBHOOK_URL=https://yourdomain.com/webhooks/korapay

# Escrow auto-release timeout (hours)
ESCROW_RELEASE_HOURS=48

# ============================================================================
# ADMIN SETTINGS
# ============================================================================

# Your Telegram ID for receiving admin notifications
ADMIN_TELEGRAM_ID=123456789

# ============================================================================
# DEPLOYMENT SETTINGS (Optional)
# ============================================================================

# Production environment indicator
# ENVIRONMENT=production

# Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
# LOG_LEVEL=INFO

# Email configuration (for notifications)
# SMTP_HOST=smtp.gmail.com
# SMTP_PORT=587
# SMTP_USER=your-email@gmail.com
# SMTP_PASSWORD=your-app-password

# AWS S3 (for storing images instead of Telegram file_ids)
# AWS_ACCESS_KEY=
# AWS_SECRET_KEY=
# AWS_BUCKET_NAME=vendoor-images

# ============================================================================
# NOTES
# ============================================================================

# Telegram file_ids are used for images (no external storage needed for MVP)
# Never commit .env to git - use .env.example as template
# For development, Docker Compose will create PostgreSQL at:
#   - Host: localhost
#   - Port: 5432
#   - Database: vendoor
#   - User: vendoor_user
#   - Password: vendoor_password
