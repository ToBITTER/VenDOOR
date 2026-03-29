web: uvicorn api.main:app --host 0.0.0.0 --port $PORT
bot: python -m bot.main
worker: celery -A celery worker --loglevel=info --concurrency=4
beat: celery -A celery beat --loglevel=info
