import os
from datetime import datetime

from fastapi import APIRouter

from src.chatbot.infrastructure.service import ChatReplyService
from src.chatbot.schema import BotInteractionRequest, TestResultsSaveRequest

router = APIRouter(prefix="/api/bot", tags=["chatbot"])

@router.post("/message")
async def bot_message(request: BotInteractionRequest):
    chatbot = ChatReplyService()
    return await chatbot.interpret_and_respond(request)

@router.post("/save-test-results")
async def save_test_results(body: TestResultsSaveRequest):
    os.makedirs("test_results", exist_ok=True)
    filename = f"test_results/run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    with open(filename, "w") as f:
        f.write(body.content)
    return {"saved_to": filename}
