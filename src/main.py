from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from src.cache import cache_flush_all, close_redis, init_redis
from src.firebase import close_firebase, init_firebase
from src.menu.loader import init_menu
from src.chatbot.exception_handlers import register_exception_handlers
from src.chatbot.router import router as chatbot_router
from src.menu.router import router as menu_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_redis()
    await cache_flush_all()
    await init_firebase()
    await init_menu()
    yield
    await close_firebase()
    await close_redis()


app = FastAPI(lifespan=lifespan)

register_exception_handlers(app)

app.include_router(chatbot_router)
app.include_router(menu_router)


@app.get("/")
async def index():
    return FileResponse("templates/index.html")
