from src.chatbot.api.schema import BotInteractionRequest, ChatbotResponse
from src.chatbot.features.visibility import ai_client as visibility_ai
from src.chatbot.features.visibility.constants import (
    RESTAURANT_CONTEXT_FALLBACK,
    RESTAURANT_CONTEXT_KEY,
)
from src.chatbot.features.visibility.utils import (
    _build_empty_order_response,
    _build_name_location,
    _build_order_lines,
    _format_order_message,
    _get_items,
    _get_restaurant_profile_fields,
    _get_restaurant_profile_json,
    build_restaurant_context,
    parse_name_location,
)
from src.menu.infrastructure.repository import menu_repository
from src.shared.cache import cache_get


class GreetingService:
    async def handle(self, request: BotInteractionRequest) -> ChatbotResponse:
        profile_json = await _get_restaurant_profile_json(request.user_id)
        profile_fields = await _get_restaurant_profile_fields(request.user_id)
        profile = {**profile_fields, **profile_json}

        greeting = profile.get("greeting", "").strip()
        if greeting:
            message = greeting
        else:
            name_location = await _build_name_location(profile)
            if name_location:
                name, location = await parse_name_location(name_location)
                if location:
                    message = (
                        f"Welcome to {name} located at {location}! "
                        "What's your name and what can I get for you today?"
                    )
                else:
                    message = (
                        f"Welcome to {name}! "
                        "What's your name and what can I get for you today?"
                    )
            else:
                message = "Welcome! What's your name and what can I get for you today?"

        return ChatbotResponse(
            chatbot_message=message,
            order_state=request.order_state,
        )


class FarewellService:
    async def handle(self, request: BotInteractionRequest) -> ChatbotResponse:
        message = await visibility_ai.handle_farewell(
            latest_message=request.latest_message,
            message_history=request.message_history,
        )
        return ChatbotResponse(chatbot_message=message, order_state=request.order_state)


class VagueMessageService:
    async def handle(self, request: BotInteractionRequest) -> ChatbotResponse:
        message = await visibility_ai.ask_clarifying_question(
            latest_message=request.latest_message,
            message_history=request.message_history,
        )
        return ChatbotResponse(chatbot_message=message, order_state=request.order_state)


class RestaurantQuestionService:
    async def handle(self, request: BotInteractionRequest) -> ChatbotResponse:
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


class MenuQuestionService:
    async def handle(self, request: BotInteractionRequest) -> ChatbotResponse:
        message = await visibility_ai.answer_menu_question(
            latest_message=request.latest_message,
            menu_context=menu_repository.get_menu_context(),
            message_history=request.message_history,
        )
        return ChatbotResponse(chatbot_message=message, order_state=request.order_state)


class MiscService:
    async def handle(self, request: BotInteractionRequest) -> ChatbotResponse:
        message = await visibility_ai.handle_misc(
            latest_message=request.latest_message,
            message_history=request.message_history,
        )
        return ChatbotResponse(chatbot_message=message, order_state=request.order_state)


class HumanEscalationService:
    async def handle(self, request: BotInteractionRequest) -> ChatbotResponse:
        return ChatbotResponse(
            chatbot_message="Of course! I'm calling a staff member over to help you now.",
            order_state=request.order_state,
            ping_for_human=True,
        )


class OrderReviewService:
    async def handle(self, request: BotInteractionRequest) -> ChatbotResponse:
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
            quantity = item.get("quantity", 1)
            modifier = item.get("modifier")
            price = await menu_repository.get_item_price(name)

            label = name
            if modifier:
                label += f" [{modifier}]"

            qty_prefix = f"{quantity}x " if quantity > 1 else ""
            if price is not None:
                line_total = price * quantity
                total += line_total
                price_str = f"(${price:.2f} each)" if quantity > 1 else f"(${price:.2f})"
                lines.append(f"- {qty_prefix}{label} {price_str} = ${line_total:.2f}")
            else:
                lines.append(f"- {qty_prefix}{label}")

        items_text = "\n".join(lines)
        total_line = f"\n\nRunning total: ${total:.2f}" if total > 0 else ""
        return ChatbotResponse(
            chatbot_message=f"Here's what you have so far:\n{items_text}{total_line}",
            order_state=request.order_state,
        )


class OrderCompletionService:
    async def handle(self, request: BotInteractionRequest) -> ChatbotResponse:
        items = await _get_items(request)
        if not items:
            return await _build_empty_order_response(request)

        lines, total = await _build_order_lines(items)
        message = await _format_order_message(lines, total)
        return ChatbotResponse(
            chatbot_message=message,
            order_state=request.order_state,
            pickup_ping=True,
        )
