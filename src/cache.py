import redis.asyncio as aioredis
from src.config import settings

redis: aioredis.Redis | None = None


async def init_redis() -> None:
    global redis
    redis = aioredis.from_url(str(settings.REDIS_URL), decode_responses=True)
    await redis.ping()
    print("Redis connected")


async def close_redis() -> None:
    global redis
    if redis:
        await redis.aclose()
        redis = None


async def cache_get(key: str) -> str | None:
    return await redis.get(key)


async def cache_set(key: str, value: str, ttl: int | None = None) -> None:
    await redis.set(key, value, ex=ttl)


async def cache_delete(key: str) -> None:
    await redis.delete(key)


async def cache_flush_all() -> None:
    await redis.flushall()
