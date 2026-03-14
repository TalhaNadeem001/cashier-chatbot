from fastapi import APIRouter
from src.chatbot.schema import BotMessageRequest
from src.chatbot.service import ChatReplyService

router = APIRouter(prefix="/api/bot", tags=["chatbot"])


@router.post("/message")
async def bot_message(request: BotMessageRequest):
    service = ChatReplyService()
    return await service.process_and_reply(request)
