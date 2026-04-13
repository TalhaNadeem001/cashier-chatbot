from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.menu.application.service import menu_service
from src.shared.cache import cache_flush_all, close_redis, init_redis
from src.shared.firebase import close_firebase, init_firebase


@asynccontextmanager
async def app_lifespan(_: FastAPI) -> AsyncIterator[None]:
    await init_redis()
    await cache_flush_all()
    await init_firebase()
    await menu_service.reload_menu_context()
    yield
    await close_firebase()
    await close_redis()
