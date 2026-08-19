from functools import lru_cache

from redis.asyncio import Redis

from .config import get_settings


@lru_cache
def get_redis() -> Redis:
    return Redis.from_url(
        get_settings().redis_url,
        encoding="utf-8",
        decode_responses=True,
    )


async def ping_redis() -> bool:
    await get_redis().ping()
    return True


async def close_redis() -> None:
    await get_redis().aclose()
