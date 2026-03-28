"""
One-time schema reset utility.

Usage in Render start command (temporary):
  sh -c "python scripts/reset_schema_once.py && alembic upgrade head && uvicorn api.main:app --host 0.0.0.0 --port $PORT"

The reset only runs when RESET_DB_ONCE=true.
"""

from __future__ import annotations

import asyncio
import os
import sys

import asyncpg


def _should_reset() -> bool:
    return os.getenv("RESET_DB_ONCE", "").strip().lower() == "true"


def _database_url() -> str:
    raw = os.getenv("DATABASE_URL", "").strip()
    if not raw:
        raise RuntimeError("DATABASE_URL is not set")
    # asyncpg expects postgresql://...
    return raw.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _reset_schema() -> None:
    conn = await asyncpg.connect(_database_url(), timeout=20)
    try:
        await conn.execute("DROP SCHEMA IF EXISTS public CASCADE;")
        await conn.execute("CREATE SCHEMA public;")
    finally:
        await conn.close()


def main() -> int:
    if not _should_reset():
        print("RESET_DB_ONCE is not true; skipping schema reset.")
        return 0

    print("RESET_DB_ONCE=true detected. Dropping and recreating public schema...")
    asyncio.run(_reset_schema())
    print("Schema reset completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
