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


async def cache_delete_pattern(pattern: str) -> None:
    keys = await redis.keys(pattern)
    if keys:
        await redis.delete(*keys)


async def cache_list_length(key: str) -> int:
    return await redis.llen(key)


async def cache_list_range(key: str, start: int, end: int) -> list[str]:
    return await redis.lrange(key, start, end)


async def cache_list_append(key: str, value: str) -> None:
    await redis.rpush(key, value)


async def cache_list_clear(key: str) -> list[str]:
    async with redis.pipeline(transaction=True) as pipe:
        await pipe.lrange(key, 0, -1)
        await pipe.delete(key)
        results = await pipe.execute()
    return results[0]
