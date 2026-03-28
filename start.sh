#!/bin/bash

# VenDOOR Quick Start Script
# Starts all services required for development

echo "🚀 Starting VenDOOR Marketplace Bot..."

# Start Docker services (PostgreSQL + Redis)
echo "📦 Starting Docker services..."
docker-compose up -d

# Wait for services to be ready
echo "⏳ Waiting for services to be ready..."
sleep 5

# Activate virtual environment
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python -m venv venv
fi

source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
echo "📚 Installing dependencies..."
pip install -r requirements.txt

# Run migrations
echo "🗄️  Running database migrations..."
alembic upgrade head

# Start services in background
echo "▶️  Starting services..."

# Bot (in a new terminal)
gnome-terminal -- python bot/main.py &

# API (in a new terminal)
gnome-terminal -- uvicorn api.main:app --reload &

# Celery Worker (in a new terminal)
gnome-terminal -- celery -A celery worker -l info &

# Celery Beat (in a new terminal)
gnome-terminal -- celery -A celery beat -l info &

echo "✅ VenDOOR is running!"
echo ""
echo "Services:"
echo "  🤖 Bot: Running (polling)"
echo "  🌐 API: http://localhost:8000"
echo "  📡 Redis: localhost:6379"
echo "  🗄️  PostgreSQL: localhost:5432"
echo ""
echo "To stop all services, press Ctrl+C or run: docker-compose down"
