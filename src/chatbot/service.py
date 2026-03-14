from src.chatbot.chatbot_ai import ChatbotAI
from src.chatbot.handlers import StateHandlerFactory
from src.chatbot.schema import BotMessageRequest, BotMessageResponse


class ChatReplyService:
    def __init__(self):
        self.ai = ChatbotAI()
        self.handler_factory = StateHandlerFactory(ai=self.ai)

    async def process_and_reply(self, request: BotMessageRequest) -> BotMessageResponse:
        state = await self.ai.determine_conversation_state(
            latest_message=request.latest_message,
            message_history=request.message_history,
        )

        return await self.handler_factory.handle(state, request)
