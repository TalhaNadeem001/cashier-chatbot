from datetime import datetime, timezone

from src.chatbot.api.schema import BotInteractionRequest, ChatbotResponse
from src.chatbot.features.intent.ai_client import extract_pickup_time_minutes


class PickupStatusService:
    async def handle(self, request: BotInteractionRequest) -> ChatbotResponse:
        return ChatbotResponse(
            chatbot_message="",
            pickup_ping=True,
            order_state=request.order_state,
        )


class PickupTimeSuggestionService:
    async def handle(self, request: BotInteractionRequest) -> ChatbotResponse:
        minutes = await extract_pickup_time_minutes(
            latest_message=request.latest_message,
            message_history=request.message_history,
        )
        timestamp = datetime.now(timezone.utc).isoformat()
        return ChatbotResponse(
            chatbot_message="Of course! We'll let you know when it's ready.",
            order_state=request.order_state,
            pickup_time_suggestion=minutes,
            pickup_time_suggestion_timestamp=timestamp,
        )
