"""
Async database session and engine creation for SQLAlchemy 2.0.
Uses asyncpg driver for PostgreSQL.
"""

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
    async_sessionmaker,
)
from sqlalchemy.pool import NullPool

from core.config import get_settings
from db.models import Base


def create_engine():
    """
    Create an async SQLAlchemy engine.
    Uses NullPool to avoid connection pooling issues in certain environments (optional).
    """
    settings = get_settings()
    
    engine = create_async_engine(
        settings.database_url,
        echo=settings.database_echo,
        future=True,
        pool_pre_ping=True,  # Verify connections are alive before using
        # pool_size=20,  # If using pool, adjust as needed
        # max_overflow=10,
        # Uncomment NullPool if you want to avoid pooling:
        # poolclass=NullPool,
    )
    return engine


def create_session_maker(engine):
    """
    Create an async session factory.
    """
    return async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )


# Global session maker (lazy initialization)
_engine = None
_session_maker = None


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Async generator that yields a database session.
    Use this in FastAPI dependency injection.
    
    Example:
        @app.get("/items/")
        async def get_items(session: AsyncSession = Depends(get_session)):
            result = await session.execute(select(Item))
            return result.scalars().all()
    """
    global _engine, _session_maker
    
    if _engine is None:
        _engine = create_engine()
        _session_maker = create_session_maker(_engine)
    
    async with _session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    """
    Initialize the database by creating all tables.
    Call this once at application startup.
    """
    global _engine
    
    if _engine is None:
        _engine = create_engine()
    
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db():
    """
    Close the database connection pool.
    Call this at application shutdown.
    """
    global _engine
    
    if _engine is not None:
        await _engine.dispose()
        _engine = None
