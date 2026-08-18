from __future__ import annotations

import logging

from redis.asyncio import Redis
from sqlalchemy import text

from app.config import Settings
from app.database import build_async_engine

logger = logging.getLogger(__name__)


async def check_dependencies(settings: Settings) -> dict[str, bool]:
    database_ok = False
    redis_ok = False
    engine = None
    try:
        engine = build_async_engine(settings.database_url)
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        database_ok = True
    except Exception as error:
        logger.warning("database healthcheck failed: %s", error)
    finally:
        if engine is not None:
            await engine.dispose()

    redis = None
    try:
        redis = Redis.from_url(settings.redis_url)
        redis_ok = bool(await redis.ping())
    except Exception as error:
        logger.warning("redis healthcheck failed: %s", error)
    finally:
        if redis is not None:
            await redis.aclose()
    return {"database": database_ok, "redis": redis_ok}
