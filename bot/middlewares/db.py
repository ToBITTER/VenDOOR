"""
Database middleware for aiogram.
Injects an async SQLAlchemy session into every handler for database access.
"""

from typing import Any, Callable, Dict, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import Message, Update
from sqlalchemy.ext.asyncio import AsyncSession

from db.session import create_session_maker, create_engine


class DatabaseMiddleware(BaseMiddleware):
    """
    Middleware that injects an async database session into handler context.
    Usage in handler:
    
        async def start_handler(message: Message, session: AsyncSession):
            user = await get_or_create_user(message, session)
            ...
    """
    
    def __init__(self):
        super().__init__()
        self.engine = None
        self.session_maker = None
    
    async def setup(self):
        """Initialize engine and session maker."""
        if self.engine is None:
            self.engine = create_engine()
            self.session_maker = create_session_maker(self.engine)
    
    async def __call__(
        self,
        handler: Callable[[Update, Dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: Dict[str, Any],
    ) -> Any:
        """
        Inject database session into handler.
        """
        await self.setup()
        
        async with self.session_maker() as session:
            data["session"] = session
            try:
                return await handler(event, data)
            finally:
                await session.close()


class FSMStorageMiddleware(BaseMiddleware):
    """
    Optional: Middleware for managing FSM state in database instead of memory.
    Useful for production deployments where bot instances may restart.
    
    For now, aiogram's default MemoryStorage is fine for development.
    """
    pass
