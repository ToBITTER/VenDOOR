"""
Main bot entry point for VenDOOR Telegram Bot.
Initializes aiogram dispatcher, registers handlers, and starts polling.
"""

import asyncio
import logging
from aiogram import Dispatcher, Bot
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeDefault

from core.config import get_settings
from db.session import close_db
from bot.middlewares.db import DatabaseMiddleware
from bot.handlers import start

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

settings = get_settings()


async def set_default_commands(bot: Bot) -> None:
    """
    Set bot commands in Telegram.
    """
    commands = [
        BotCommand(command="start", description="Start the bot"),
        BotCommand(command="help", description="Get help"),
        BotCommand(command="my_orders", description="View my orders"),
        BotCommand(command="my_listings", description="View my listings (sellers)"),
        BotCommand(command="sell", description="Register as seller"),
    ]
    await bot.set_my_commands(commands, BotCommandScopeDefault())


async def on_startup(dispatcher: Dispatcher, bot: Bot) -> None:
    """
    Called when bot starts. Set commands and verify runtime readiness.
    """
    logger.info("Starting VenDOOR Bot...")
    
    # Set bot commands
    await set_default_commands(bot)
    logger.info("Bot commands set")


async def on_shutdown(dispatcher: Dispatcher, bot: Bot) -> None:
    """
    Called when bot shuts down. Clean up resources.
    """
    logger.info("Shutting down VenDOOR Bot...")
    
    # Close database connection
    await close_db()
    logger.info("Database connection closed")


async def main():
    """
    Main entry point. Initialize bot and dispatcher.
    """
    # Initialize bot and dispatcher
    bot = Bot(token=settings.telegram_bot_token)
    storage = MemoryStorage()  # Use Redis for production
    dispatcher = Dispatcher(storage=storage)
    
    # Add middlewares
    db_middleware = DatabaseMiddleware()
    dispatcher.message.middleware(db_middleware)
    dispatcher.callback_query.middleware(db_middleware)
    
    # Register routers
    dispatcher.include_router(start.router)
    
    # Import handler modules
    from bot.handlers.seller import register as seller_register
    from bot.handlers.seller import listings as seller_listings
    from bot.handlers.buyer import catalog as buyer_catalog
    from bot.handlers.buyer import checkout as buyer_checkout
    from bot.handlers.buyer import orders as buyer_orders
    from bot.handlers import complaints
    
    # Register all routers
    dispatcher.include_router(seller_register.router)
    dispatcher.include_router(seller_listings.router)
    dispatcher.include_router(buyer_catalog.router)
    dispatcher.include_router(buyer_checkout.router)
    dispatcher.include_router(buyer_orders.router)
    dispatcher.include_router(complaints.router)
    
    # Set startup/shutdown handlers
    dispatcher.startup.register(lambda disp: on_startup(disp, bot))
    dispatcher.shutdown.register(lambda disp: on_shutdown(disp, bot))
    
    logger.info("Starting bot polling...")
    try:
        # Start polling
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
