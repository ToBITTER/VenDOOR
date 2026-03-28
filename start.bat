@echo off
REM VenDOOR Quick Start Script for Windows
REM Starts Docker services and the bot in development mode

echo.
echo ╔════════════════════════════════════════════════════════════╗
echo ║         🎉 VenDOOR Marketplace Bot - Quick Start          ║
echo ╚════════════════════════════════════════════════════════════╝
echo.

REM Check if Docker is running
docker info >nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ Docker is not running. Please start Docker Desktop first.
    pause
    exit /b 1
)

REM Start Docker services
echo 📦 Starting Docker services (PostgreSQL + Redis)...
docker-compose up -d
if %errorlevel% neq 0 (
    echo ❌ Failed to start Docker services
    pause
    exit /b 1
)

REM Wait for services
echo ⏳ Waiting for services to be ready (30 seconds)...
timeout /t 30 /nobreak

REM Check virtual environment
if not exist "venv" (
    echo 📦 Creating Python virtual environment...
    python -m venv venv
)

REM Activate venv
call venv\Scripts\activate.bat

REM Install dependencies
echo 📚 Installing Python dependencies...
pip install -r requirements.txt >nul 2>&1

REM Run migrations
echo 🗄️  Running database migrations...
alembic upgrade head

REM Show menu
:menu
echo.
echo ╔════════════════════════════════════════════════════════════╗
echo ║              Select which service to start                ║
echo ╠════════════════════════════════════════════════════════════╣
echo ║  1. 🤖  Start Bot Only                                      ║
echo ║  2. 🌐 Start API Only                                       ║
echo ║  3. 📡 Start Celery Worker                                  ║
echo ║  4. ⏰ Start Celery Beat (scheduler)                        ║
echo ║  5. 🚀 Start All Services (experimental)                    ║
echo ║  6. 📊 View Docker Logs                                     ║
echo ║  7. ❌ Stop All Services                                    ║
echo ║  8. 🚪 Exit                                                 ║
echo ╚════════════════════════════════════════════════════════════╝
echo.

set /p choice="Enter your choice (1-8): "

if "%choice%"=="1" (
    echo.
    echo ▶️  Starting Bot...
    echo 📍 Polling Telegram for messages...
    python bot/main.py
    goto menu
)

if "%choice%"=="2" (
    echo.
    echo ▶️  Starting API...
    echo 🌐 http://localhost:8000
    echo 📄 Docs: http://localhost:8000/docs
    uvicorn api.main:app --reload
    goto menu
)

if "%choice%"=="3" (
    echo.
    echo ▶️  Starting Celery Worker...
    celery -A celery worker -l info
    goto menu
)

if "%choice%"=="4" (
    echo.
    echo ▶️  Starting Celery Beat...
    celery -A celery beat -l info
    goto menu
)

if "%choice%"=="5" (
    echo.
    echo ⚠️  Starting all services...
    echo Note: This requires multiple terminals. Use individual commands instead.
    echo.
    echo Copy and run these in separate terminals:
    echo.
    echo   Terminal 1: python bot/main.py
    echo   Terminal 2: uvicorn api.main:app --reload
    echo   Terminal 3: celery -A celery worker -l info
    echo   Terminal 4: celery -A celery beat -l info
    echo.
    pause
    goto menu
)

if "%choice%"=="6" (
    echo.
    docker-compose logs -f
)

if "%choice%"=="7" (
    echo.
    echo 🛑 Stopping all Docker services...
    docker-compose down
    echo ✅ Services stopped
    echo.
    goto menu
)

if "%choice%"=="8" (
    echo.
    echo 👋 Goodbye!
    exit /b 0
)

echo ❌ Invalid choice. Please try again.
goto menu
