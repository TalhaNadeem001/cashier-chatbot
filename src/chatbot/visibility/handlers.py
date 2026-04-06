import json

from src.cache import cache_get
from src.chatbot.cart.handlers import OrderStateHandler, ModifierStateHandler
from src.chatbot.constants import ConversationState
from src.chatbot.exceptions import UnhandledStateError
from src.chatbot.schema import BotInteractionRequest, ChatbotResponse
from src.chatbot.visibility import ai_client as visibility_ai
from src.menu.loader import get_item_price, get_menu_context

RESTAURANT_CONTEXT_KEY = "restaurant_context:{user_id}"
RESTAURANT_CONTEXT_FALLBACK = "No specific restaurant information is available at this time."

RESTAURANT_NAME_LOCATION_KEY = "restaurant_name_location:{user_id}"
RESTAURANT_NAME_LOCATION_FALLBACK = "No restaurant name or location is available at this time."
RESTAURANT_CONTEXT_JSON_KEY = "restaurantContext:{user_id}"
RESTAURANT_NAME_KEY = "restaurant_name:{user_id}"
RESTAURANT_CITY_KEY = "restaurant_city:{user_id}"
RESTAURANT_PHONE_KEY = "restaurant_phone:{user_id}"
RESTAURANT_TAGLINE_KEY = "restaurant_tagline:{user_id}"
RESTAURANT_GREETING_KEY = "restaurant_greeting:{user_id}"


async def _get_restaurant_profile_fields(user_id: str) -> dict[str, str]:
    name = await cache_get(RESTAURANT_NAME_KEY.format(user_id=user_id))
    city = await cache_get(RESTAURANT_CITY_KEY.format(user_id=user_id))
    phone = await cache_get(RESTAURANT_PHONE_KEY.format(user_id=user_id))
    tagline = await cache_get(RESTAURANT_TAGLINE_KEY.format(user_id=user_id))
    greeting = await cache_get(RESTAURANT_GREETING_KEY.format(user_id=user_id))
    return {
        "restaurantName": name or "",
        "city": city or "",
        "phone": phone or "",
        "tagline": tagline or "",
        "greeting": greeting or "",
    }


async def _get_restaurant_profile_json(user_id: str) -> dict[str, str]:
    raw = await cache_get(RESTAURANT_CONTEXT_JSON_KEY.format(user_id=user_id))
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if isinstance(parsed, dict):
        return {k: str(v) for k, v in parsed.items() if v is not None}
    return {}


def _build_name_location(profile: dict[str, str]) -> str | None:
    name_location = profile.get("nameLocation")
    if name_location:
        return name_location

    name = profile.get("restaurantName", "").strip()
    city = profile.get("city", "").strip()
    if name and city:
        return f"{name}, {city}"
    if name:
        return name
    return None


def _build_restaurant_context(profile: dict[str, str]) -> str | None:
    lines: list[str] = []
    name = profile.get("restaurantName", "").strip()
    tagline = profile.get("tagline", "").strip()
    phone = profile.get("phone", "").strip()
    city = profile.get("city", "").strip()
    greeting = profile.get("greeting", "").strip()
    name_location = _build_name_location(profile)

    if name:
        lines.append(f"Restaurant name: {name}")
    if name_location:
        lines.append(f"Location: {name_location}")
    if city:
        lines.append(f"City: {city}")
    if phone:
        lines.append(f"Phone: {phone}")
    if tagline:
        lines.append(f"Tagline: {tagline}")
    if greeting:
        lines.append(f"Greeting: {greeting}")

    if lines:
        return "\n".join(lines)
    return None


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
            ConversationState.ADDING_MODIFIERS: self._handle_modifiers,
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

    async def _handle_greeting(self, request: BotInteractionRequest) -> ChatbotResponse:
        restaurant_name_location = await cache_get(
            RESTAURANT_NAME_LOCATION_KEY.format(user_id=request.user_id)
        )
        if not restaurant_name_location:
            profile_json = await _get_restaurant_profile_json(request.user_id)
            profile_fields = await _get_restaurant_profile_fields(request.user_id)
            merged_profile = {**profile_fields, **profile_json}
            restaurant_name_location = _build_name_location(merged_profile)

        restaurant_name_location = restaurant_name_location or RESTAURANT_NAME_LOCATION_FALLBACK

        parts = restaurant_name_location.split(',', 1)
        if len(parts) == 2:
            welcome_msg = f"Welcome to {parts[0].strip()} located at {parts[1].strip()}! What's your name and what can I get for you today?"
        else:
            welcome_msg = f"Welcome to {parts[0].strip()}! What's your name and what can I get for you today?"
        return ChatbotResponse(
            chatbot_message=welcome_msg,
            order_state=request.order_state,
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
            restaurant_context = _build_restaurant_context(merged_profile)

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
        response = await self.current_order_state.handle(request)
        return response
    
    async def _handle_modifiers(self, request: BotInteractionRequest) -> ChatbotResponse:
        response = await self.current_modifier_state.handle(request)
        return response

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
        items = await self._get_items(request)
        
        if not items:
            return await self._build_empty_order_response(request)

        lines, total = await self._build_order_lines(items)
        message = await self._format_order_message(lines, total)

        return ChatbotResponse(
            chatbot_message=message,
            order_state=request.order_state,
            pickup_ping=True,
        )


    async def _get_items(self, request: BotInteractionRequest) -> list[dict]:
        return (request.order_state or {}).get("items", [])


    async def _build_order_lines(self, items: list[dict]) -> tuple[list[str], float]:
        lines: list[str] = []
        total = 0.0

        for item in items:
            line, line_total = await self._format_line_item(item)

            if line_total is not None:
                total += line_total

            lines.append(line)

        return lines, total

    async def _format_line_item(self, item: dict) -> tuple[str, float | None]:
        name = item.get("name", "Unknown item")
        quantity = item.get("quantity", 1)
        modifier = item.get("modifier")

        price = await get_item_price(name)

        label = await self._build_item_label(name, price, quantity, modifier)

        qty_prefix = f"{quantity}x " if quantity > 1 else ""

        if price is None:
            return f"- {qty_prefix}{label}", None

        line_total = price * quantity
        return f"- {qty_prefix}{label} = ${line_total:.2f}", line_total

    async def _build_item_label(self, name: str, price: float | None, quantity: int, modifier: str | None,) -> str:
        if price is not None:
            if quantity > 1:
                label = f"{name} (${price:.2f} each)"
            else:
                label = f"{name} (${price:.2f})"
        else:
            label = name

        if modifier:
            label += f" [{modifier}]"

        return label


    async def _format_order_message(self, lines: list[str], total: float) -> str:
        items_text = "\n".join(lines)
        total_line = f"\n\nTotal: ${total:.2f}" if total > 0 else ""

        return (
            f"Great! Your order is:\n"
            f"{items_text}"
            f"{total_line}\n\n"
            f"Thank you for ordering!"
        )


    async def _build_empty_order_response(self, request: BotInteractionRequest) -> ChatbotResponse:
        return ChatbotResponse(
            chatbot_message="It looks like you haven't ordered anything yet — what would you like?",
            order_state=request.order_state,
        )
