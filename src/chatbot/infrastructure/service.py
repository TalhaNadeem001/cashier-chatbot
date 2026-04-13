from src.chatbot.infrastructure.summarizer import compress_history_if_needed
from src.chatbot.intent.resolver import ConversationStateResolver
from src.chatbot.schema import BotInteractionRequest, ChatbotResponse
from src.chatbot.visibility.handlers import StateHandlerFactory


class ChatReplyService:
    def __init__(self):
        self.conversation_engine = StateHandlerFactory()
        self.chatbot = ConversationStateResolver()

    async def interpret_and_respond(self, Conversation: BotInteractionRequest) -> ChatbotResponse:
        print("[chat] request.latest_message:", Conversation.latest_message)
        print("[chat] request.previous_state:", Conversation.previous_state)
        print("[chat] request.order_state:", Conversation.order_state)

        message_history = await compress_history_if_needed(
            user_id=Conversation.user_id,
            message_history=Conversation.message_history,
        )
        print("[chat] compressed_history_count:", len(message_history or []))

        state = await self.chatbot.resolve_user_intent(
            latest_message=Conversation.latest_message,
            message_history=message_history,
            previous_state=Conversation.previous_state,
        )
        print("[chat] resolved_conversation_state:", state)

        response = await self.conversation_engine.respond_to_message(state, Conversation)
        response.previous_state = state.value
        print("[chat] response.chatbot_message:", response.chatbot_message)
        print("[chat] response.order_state:", response.order_state)
        return response
