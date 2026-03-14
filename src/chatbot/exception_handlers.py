from fastapi import Request, status
from fastapi.responses import JSONResponse
from src.chatbot.exceptions import AIServiceError, InvalidConversationStateError, UnhandledStateError


async def ai_service_error_handler(request: Request, exc: AIServiceError) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"detail": str(exc)})


async def invalid_conversation_state_handler(request: Request, exc: InvalidConversationStateError) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content={"detail": str(exc)})


async def unhandled_state_handler(request: Request, exc: UnhandledStateError) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"detail": str(exc)})


def register_exception_handlers(app) -> None:
    app.add_exception_handler(InvalidConversationStateError, invalid_conversation_state_handler)
    app.add_exception_handler(AIServiceError, ai_service_error_handler)
    app.add_exception_handler(UnhandledStateError, unhandled_state_handler)
