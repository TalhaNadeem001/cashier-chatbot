from src.cache import cache_get
from src.chatbot.cart.handlers import OrderStateHandler, ModifierStateHandler
from src.chatbot.constants import ConversationState
from src.chatbot.exceptions import UnhandledStateError
from src.chatbot.schema import BotInteractionRequest, ChatbotResponse

from src.chatbot.visibility import ai_client as visibility_ai
from src.menu.loader import get_menu_context
from src.chatbot.visibility.constants import (
    RESTAURANT_NAME_LOCATION_KEY,
    RESTAURANT_NAME_LOCATION_FALLBACK,
    RESTAURANT_CONTEXT_KEY,
    RESTAURANT_CONTEXT_FALLBACK,
)
from src.chatbot.visibility.utils import _get_restaurant_profile_json, _get_restaurant_profile_fields, _build_name_location, build_restaurant_context, fetch_restaurant_profile, parse_name_location, _get_items, _build_order_lines, _format_order_message, _build_empty_order_response

class StateHandlerFactory:
    def __init__(self):
        self.current_order_state = OrderStateHandler()
        self.current_modifier_state = ModifierStateHandler()
        self._handlers = {
            ConversationState.GREETING: self._handle_greeting,
            ConversationState.FAREWELL: self._handle_farewell,
            ConversationState.VAGUE_MESSAGE: self._handle_vague_message,
            ConversationState.RESTAURANT_QUESTION: self._handle_restaurant_question,
            ConversationState.MENU_QUESTION: self._handle_menu_question,
            ConversationState.FOOD_ORDER: self._handle_food_order,
            ConversationState.PICKUP_PING: self._handle_pickup_ping,
            ConversationState.MISC: self._handle_misc,
            ConversationState.HUMAN_ESCALATION: self._handle_human_escalation,
            ConversationState.ORDER_COMPLETE: self._handle_order_complete,
        }

    async def respond_to_message(self, state: ConversationState, request: BotInteractionRequest) -> ChatbotResponse:
        handler = self._handlers.get(state)
        if handler is None:
            raise UnhandledStateError(f"No handler registered for state: '{state}'")
        return await handler(request)

    async def _handle_greeting( self, request: BotInteractionRequest) -> ChatbotResponse:

        restaurant_info = await self._get_restaurant_info(request.user_id)
        message = await self._build_welcome_message(restaurant_info)

        return ChatbotResponse(
            chatbot_message=message,
            order_state=request.order_state,
        )
    
    async def _get_restaurant_info(self, user_id: str) -> str:
        cached = await cache_get(
            RESTAURANT_NAME_LOCATION_KEY.format(user_id=user_id)
        )
        if cached:
            return cached

        profile = await fetch_restaurant_profile(user_id)
        return await _build_name_location(profile) or RESTAURANT_NAME_LOCATION_FALLBACK
    
    async def _build_welcome_message(self, restaurant_info: str) -> str:
        name, location = await parse_name_location(restaurant_info)

        if location:
            return (
                f"Welcome to {name} located at {location}! "
                "What's your name and what can I get for you today?"
            )

        return (
            f"Welcome to {name}! "
            "What's your name and what can I get for you today?"
        )
    
    async def _handle_farewell(self, request: BotInteractionRequest) -> ChatbotResponse:
        message = await visibility_ai.handle_farewell(
            latest_message=request.latest_message,
            message_history=request.message_history,
        )
        return ChatbotResponse(chatbot_message=message, order_state=request.order_state)

    async def _handle_vague_message(self, request: BotInteractionRequest) -> ChatbotResponse:
        message = await visibility_ai.ask_clarifying_question(
            latest_message=request.latest_message,
            message_history=request.message_history,
        )
        return ChatbotResponse(chatbot_message=message, order_state=request.order_state)

    async def _handle_restaurant_question(self, request: BotInteractionRequest) -> ChatbotResponse:
        restaurant_context = await cache_get(
            RESTAURANT_CONTEXT_KEY.format(user_id=request.user_id)
        )
        if not restaurant_context:
            profile_json = await _get_restaurant_profile_json(request.user_id)
            profile_fields = await _get_restaurant_profile_fields(request.user_id)
            merged_profile = {**profile_fields, **profile_json}
            restaurant_context = await build_restaurant_context(merged_profile)

        restaurant_context = restaurant_context or RESTAURANT_CONTEXT_FALLBACK

        message = await visibility_ai.answer_restaurant_question(
            latest_message=request.latest_message,
            restaurant_context=restaurant_context,
            message_history=request.message_history,
        )
        return ChatbotResponse(chatbot_message=message, order_state=request.order_state)

    async def _handle_menu_question(self, request: BotInteractionRequest) -> ChatbotResponse:
        menu_context = get_menu_context()
        message = await visibility_ai.answer_menu_question(
            latest_message=request.latest_message,
            menu_context=menu_context,
            message_history=request.message_history,
        )
        return ChatbotResponse(chatbot_message=message, order_state=request.order_state)

    async def _handle_food_order(self, request: BotInteractionRequest) -> ChatbotResponse:
        food_response = await self.current_order_state.handle(request)
        updated_request = request.model_copy(update={"order_state": food_response.order_state})
        return await self.current_modifier_state.handle(updated_request, food_response)
    
    async def _handle_modifiers(self, request: BotInteractionRequest) -> ChatbotResponse:
        return await self.current_order_state.handle(request)

    async def _handle_pickup_ping(self, request: BotInteractionRequest) -> ChatbotResponse:
        return ChatbotResponse(chatbot_message="", pickup_ping=True, order_state=request.order_state)

    async def _handle_human_escalation(self, request: BotInteractionRequest) -> ChatbotResponse:
        return ChatbotResponse(
            chatbot_message="Of course! I'm calling a staff member over to help you now.",
            order_state=request.order_state,
            ping_for_human=True,
        )

    async def _handle_misc(self, request: BotInteractionRequest) -> ChatbotResponse:
        message = await visibility_ai.handle_misc(
            latest_message=request.latest_message,
            message_history=request.message_history,
        )
        return ChatbotResponse(chatbot_message=message, order_state=request.order_state)

    async def _handle_order_complete(self, request: BotInteractionRequest) -> ChatbotResponse:
        items = await _get_items(request)

        if not items:
            return await _build_empty_order_response(request)

        lines, total = await _build_order_lines(items)
        message = await _format_order_message(lines, total)

        return ChatbotResponse(chatbot_message=message, order_state=request.order_state, pickup_ping=True)
