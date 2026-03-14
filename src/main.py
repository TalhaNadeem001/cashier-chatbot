from contextlib import asynccontextmanager
from fastapi import FastAPI
from src.cache import close_redis, init_redis
from src.chatbot.exception_handlers import register_exception_handlers
from src.chatbot.router import router as chatbot_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_redis()
    yield
    await close_redis()


app = FastAPI(lifespan=lifespan)

register_exception_handlers(app)

app.include_router(chatbot_router)
