from src.chatbot.intent.resolver import ConversationStateResolver
from src.chatbot.schema import BotInteractionRequest, ChatbotResponse
from src.chatbot.visibility.handlers import StateHandlerFactory

class ChatReplyService:
    def __init__(self):
        self.conversation_engine = StateHandlerFactory()
        self.chatbot = ConversationStateResolver()

    async def interpret_and_respond(self, Conversation: BotInteractionRequest) -> ChatbotResponse:
        user_name = await self.chatbot.get_user_name(Conversation.message_history, Conversation.latest_message, Conversation.customer_name)
        state = await self.chatbot.resolve_user_intent(
            latest_message=Conversation.latest_message,
            message_history=Conversation.message_history,
            previous_state=Conversation.previous_state,
        )
        response = await self.conversation_engine.respond_to_message(state, Conversation)
        response.previous_state = state.value
        response.customer_name = user_name
        return response
