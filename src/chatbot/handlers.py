from src.cache import cache_get
from src.chatbot.chatbot_ai import ChatbotAI
from src.chatbot.constants import ConversationState
from src.chatbot.exceptions import UnhandledStateError
from src.chatbot.food_order_handlers import FoodOrderHandlerFactory
from src.chatbot.schema import BotMessageRequest, BotMessageResponse

RESTAURANT_CONTEXT_KEY = "restaurant_context:{user_id}"
RESTAURANT_CONTEXT_FALLBACK = "No specific restaurant information is available at this time."

MENU_CONTEXT_KEY = "menu_context:{user_id}"
MENU_CONTEXT_FALLBACK = "No menu information is available at this time."


class StateHandlerFactory:
    def __init__(self, ai: ChatbotAI):
        self._ai = ai
        self._food_order_factory = FoodOrderHandlerFactory(ai=ai)
        self._handlers = {
            ConversationState.VAGUE_MESSAGE: self._handle_vague_message,
            ConversationState.RESTAURANT_QUESTION: self._handle_restaurant_question,
            ConversationState.MENU_QUESTION: self._handle_menu_question,
            ConversationState.FOOD_ORDER: self._handle_food_order,
            ConversationState.PICKUP_PING: self._handle_pickup_ping,
            ConversationState.MISC: self._handle_misc,
        }

    async def handle(self, state: ConversationState, request: BotMessageRequest) -> BotMessageResponse:
        handler = self._handlers.get(state)
        if handler is None:
            raise UnhandledStateError(f"No handler registered for state: '{state}'")
        return await handler(request)

    async def _handle_vague_message(self, request: BotMessageRequest) -> BotMessageResponse:
        message = await self._ai.ask_clarifying_question(
            latest_message=request.latest_message,
            message_history=request.message_history,
        )
        return BotMessageResponse(chatbot_message=message, order_state=request.order_state)

    async def _handle_restaurant_question(self, request: BotMessageRequest) -> BotMessageResponse:
        restaurant_context = await cache_get(
            RESTAURANT_CONTEXT_KEY.format(user_id=request.user_id)
        ) or RESTAURANT_CONTEXT_FALLBACK

        message = await self._ai.answer_restaurant_question(
            latest_message=request.latest_message,
            restaurant_context=restaurant_context,
            message_history=request.message_history,
        )
        return BotMessageResponse(chatbot_message=message, order_state=request.order_state)

    async def _handle_menu_question(self, request: BotMessageRequest) -> BotMessageResponse:
        menu_context = await cache_get(
            MENU_CONTEXT_KEY.format(user_id=request.user_id)
        ) or MENU_CONTEXT_FALLBACK

        message = await self._ai.answer_menu_question(
            latest_message=request.latest_message,
            menu_context=menu_context,
            message_history=request.message_history,
        )
        return BotMessageResponse(chatbot_message=message, order_state=request.order_state)

    async def _handle_food_order(self, request: BotMessageRequest) -> BotMessageResponse:
        return await self._food_order_factory.handle(request)

    async def _handle_pickup_ping(self, request: BotMessageRequest) -> BotMessageResponse:
        return BotMessageResponse(chatbot_message=ConversationState.PICKUP_PING, pickup_ping=True, order_state=request.order_state)

    async def _handle_misc(self, request: BotMessageRequest) -> BotMessageResponse:
        message = await self._ai.handle_misc(
            latest_message=request.latest_message,
            message_history=request.message_history,
        )
        return BotMessageResponse(chatbot_message=message, order_state=request.order_state)
