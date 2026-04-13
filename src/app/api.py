from fastapi import FastAPI
from fastapi.responses import FileResponse

from src.chatbot.api.exception_handlers import register_exception_handlers
from src.chatbot.api.router import router as chatbot_router
from src.menu.api.router import router as menu_router


def register_routes(app: FastAPI) -> None:
    register_exception_handlers(app)
    app.include_router(chatbot_router)
    app.include_router(menu_router)

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse("templates/index.html")
