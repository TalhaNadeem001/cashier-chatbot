from src.chatbot.chatbot_ai import ChatbotAI
from src.chatbot.constants import ConversationState
from src.chatbot.handlers import StateHandlerFactory
from src.chatbot.schema import BotMessageRequest, BotMessageResponse
from src.chatbot.state_resolver import StateResolver


def _parse_safely(value: str | None, enum_cls):
    if not value:
        return None
    try:
        return enum_cls(value.strip().lower())
    except ValueError:
        return None


class ChatReplyService:
    def __init__(self):
        self.ai = ChatbotAI()
        self.handler_factory = StateHandlerFactory(ai=self.ai)
        self.resolver = StateResolver(ai=self.ai)

    async def process_and_reply(self, request: BotMessageRequest) -> BotMessageResponse:
        response = await self._build_reply(request)
        print(f"Response: {response.model_dump()}")
        return response

    async def _build_reply(self, request: BotMessageRequest) -> BotMessageResponse:
        if request.awaiting_order_confirmation:
            finalization = await self.ai.resolve_order_finalization(
                latest_message=request.latest_message,
                order_state=request.order_state or {},
                message_history=request.message_history,
            )
            if finalization.intent == "confirm":
                return BotMessageResponse(
                    chatbot_message="Perfect! Your order is placed. We'll have it ready shortly!",
                    pickup_ping=True,
                    order_state=request.order_state,
                    previous_state=ConversationState.FINALIZING_ORDER.value,
                    customer_name=request.customer_name,
                )
            elif finalization.intent == "modify":
                request = request.model_copy(update={"awaiting_order_confirmation": False})
                # fall through to normal state resolution below
            else:  # unclear
                return BotMessageResponse(
                    chatbot_message="Just to confirm — shall I go ahead and place your order?",
                    order_state=request.order_state,
                    awaiting_order_confirmation=True,
                    previous_state=ConversationState.FINALIZING_ORDER.value,
                    customer_name=request.customer_name,
                )

        previous_state = _parse_safely(request.previous_state, ConversationState)
        state, extracted_name = await self.resolver.resolve(
            latest_message=request.latest_message,
            message_history=request.message_history,
            previous_state=previous_state,
            has_pending_clarification=request.has_pending_clarification,
        )
        customer_name = extracted_name or request.customer_name
        response = await self.handler_factory.handle(state, request)
        if response.awaiting_order_confirmation:
            response.previous_state = ConversationState.FINALIZING_ORDER.value
        else:
            response.previous_state = state.value
        response.customer_name = customer_name
        return response
