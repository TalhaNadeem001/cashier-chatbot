from src.cache import cache_get
from src.chatbot.cart.handlers import OrderStateHandler
from src.chatbot.constants import ConversationState
from src.chatbot.exceptions import UnhandledStateError
from src.chatbot.schema import BotInteractionRequest, ChatbotResponse

from src.chatbot.visibility import ai_client as visibility_ai
from src.menu.loader import (
    get_menu_context,
    get_menu_hours_context,
    get_order_item_line_total,
    get_order_item_unit_price,
    order_item_uses_quantity_selection,
)
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
        self._handlers = {
            ConversationState.GREETING: self._handle_greeting,
            ConversationState.FAREWELL: self._handle_farewell,
            ConversationState.VAGUE_MESSAGE: self._handle_vague_message,
            ConversationState.RESTAURANT_QUESTION: self._handle_restaurant_question,
            ConversationState.MENU_QUESTION: self._handle_menu_question,
            ConversationState.FOOD_ORDER: self._handle_food_order,
            ConversationState.PICKUP_PING: self._handle_pickup_ping,
            ConversationState.PICKUP_TIME_SUGGESTION: self._handle_pickup_time_suggestion,
            ConversationState.MISC: self._handle_misc,
            ConversationState.HUMAN_ESCALATION: self._handle_human_escalation,
            ConversationState.ORDER_COMPLETE: self._handle_order_complete,
            ConversationState.ORDER_REVIEW: self._handle_order_review,
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
            chatbot_message="Smash n Wings, This is our store: 3717 Monroe street, Dearborn, Ml 48124. Please text your order including a name and confirm the given pick up time. Thank you.",
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
        menu_hours = get_menu_hours_context()
        if menu_hours and "hour" not in restaurant_context.lower():
            restaurant_context = f"{restaurant_context}\nHours:\n{menu_hours}"

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
        print("[visibility] entering_food_order_handler")
        return await self.current_order_state.handle(request)

    async def _handle_pickup_ping(self, request: BotInteractionRequest) -> ChatbotResponse:
        return ChatbotResponse(chatbot_message="", pickup_ping=True, order_state=request.order_state)

    async def _handle_pickup_time_suggestion(self, request: BotInteractionRequest) -> ChatbotResponse:
        from datetime import datetime, timezone
        from src.chatbot.intent.ai_client import extract_pickup_time_minutes

        minutes = await extract_pickup_time_minutes(
            latest_message=request.latest_message,
            message_history=request.message_history,
        )
        timestamp = datetime.now(timezone.utc).isoformat()
        print("minutes", minutes)
        print("timestamp", timestamp)

        return ChatbotResponse(
            chatbot_message="Of course! We'll let you know when it's ready.",
            order_state=request.order_state,
            pickup_time_suggestion=minutes,
            pickup_time_suggestion_timestamp=timestamp,
        )

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

    async def _handle_order_review(self, request: BotInteractionRequest) -> ChatbotResponse:
        items = (request.order_state or {}).get("items", [])
        if not items:
            return ChatbotResponse(
                chatbot_message="Your order is empty. What would you like to order?",
                order_state=request.order_state,
            )

        lines: list[str] = []
        total = 0.0
        for item in items:
            name = item.get("name", "Unknown item")
            modifier = item.get("modifier")
            quantity = int(item.get("quantity", 1) or 1)
            raw_unit_price = get_order_item_unit_price(item)
            raw_line_total = get_order_item_line_total(item)
            price = raw_unit_price / 100 if raw_unit_price is not None else None
            line_total = raw_line_total / 100 if raw_line_total is not None else None
            quantity_is_selection = order_item_uses_quantity_selection(item)

            label = name
            if modifier:
                label += f" [{modifier}]"

            qty_prefix = f"{quantity}x " if quantity > 1 and not quantity_is_selection else ""
            if price is not None and line_total is not None:
                total += line_total
                price_str = f"(${price:.2f} each)" if quantity > 1 and not quantity_is_selection else f"(${price:.2f})"
                lines.append(f"- {qty_prefix}{label} {price_str} = ${line_total:.2f}")
            else:
                lines.append(f"- {qty_prefix}{label}")

        items_text = "\n".join(lines)
        total_line = f"\n\nRunning total: ${total:.2f}" if total > 0 else ""
        message = f"Here's what you have so far:\n{items_text}{total_line}"

        return ChatbotResponse(
            chatbot_message=message,
            order_state=request.order_state,
        )

    async def _handle_order_complete(self, request: BotInteractionRequest) -> ChatbotResponse:
        items = await _get_items(request)

        if not items:
            return await _build_empty_order_response(request)

        lines, total = await _build_order_lines(items)
        customer_label = str((request.order_state or {}).get("customer_label", "")).strip() or None
        message = await _format_order_message(lines, total, customer_label=customer_label)

        return ChatbotResponse(chatbot_message=message, order_state=request.order_state, pickup_ping=True)
