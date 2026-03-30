"""
Shared bot application setup.
Used by both polling mode and webhook mode.
"""

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from core.config import get_settings
from bot.middlewares.db import DatabaseMiddleware
from bot.handlers import start
from bot.handlers.seller import register as seller_register
from bot.handlers.seller import listings as seller_listings
from bot.handlers.buyer import catalog as buyer_catalog
from bot.handlers.buyer import checkout as buyer_checkout
from bot.handlers.buyer import orders as buyer_orders
from bot.handlers import complaints
from bot.handlers import admin as admin_handlers
from bot.handlers import fallback as fallback_handlers

settings = get_settings()


def create_bot() -> Bot:
    return Bot(token=settings.telegram_bot_token)


def create_dispatcher() -> Dispatcher:
    storage = MemoryStorage()
    dispatcher = Dispatcher(storage=storage)

    db_middleware = DatabaseMiddleware()
    dispatcher.message.middleware(db_middleware)
    dispatcher.callback_query.middleware(db_middleware)

    dispatcher.include_router(start.router)
    dispatcher.include_router(seller_register.router)
    dispatcher.include_router(seller_listings.router)
    dispatcher.include_router(buyer_catalog.router)
    dispatcher.include_router(buyer_checkout.router)
    dispatcher.include_router(buyer_orders.router)
    dispatcher.include_router(complaints.router)
    dispatcher.include_router(admin_handlers.router)
    dispatcher.include_router(fallback_handlers.router)

    return dispatcher
