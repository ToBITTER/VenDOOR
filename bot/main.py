"""
Main bot entry point for VenDOOR Telegram Bot.
Initializes aiogram dispatcher, registers handlers, and starts polling.
"""

import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault

from core.config import get_settings
from db.session import close_db
from bot.app import create_bot, create_dispatcher

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

    if settings.admin_telegram_id:
        try:
            admin_chat_id = int(str(settings.admin_telegram_id).strip())
        except (TypeError, ValueError):
            logger.warning("Skipping admin command scope: invalid ADMIN_TELEGRAM_ID")
            return
        admin_commands = [
            BotCommand(command="pending_sellers", description="Review pending seller approvals"),
            BotCommand(command="admin_tools", description="Open admin tools panel"),
        ]
        await bot.set_my_commands(
            admin_commands,
            BotCommandScopeChat(chat_id=admin_chat_id),
        )


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
    bot = create_bot()
    dispatcher = create_dispatcher()
    
    # Initialize bot instance for notification service
    try:
        from services.delivery_notifications import set_bot_instance

        set_bot_instance(bot)
    except Exception:
        logger.exception("Failed to initialize delivery notification bot instance")
    
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
