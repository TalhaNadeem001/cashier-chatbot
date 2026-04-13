from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.app.api import register_routes
from src.app.lifespan import app_lifespan


@asynccontextmanager
async def _noop_lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield


def create_app(*, use_lifespan: bool = True) -> FastAPI:
    lifespan = app_lifespan if use_lifespan else _noop_lifespan
    app = FastAPI(lifespan=lifespan)
    register_routes(app)
    return app


app = create_app()
