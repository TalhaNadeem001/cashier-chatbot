from __future__ import annotations

from src.chatbot.cart import ai_client as cart_ai
from src.chatbot.extraction import ai_client as extraction_ai
from src.chatbot.intent import ai_client as intent_ai
from src.chatbot.schema import Message
from src.chatbot.visibility import ai_client as visibility_ai


class ChatbotAI:
    async def detectUserIntent(
        self,
        latest_message: str,
        message_history: list[Message] | None = None,
        previous_state: str | None = None,
    ):
        return await intent_ai.detect_user_intent(latest_message, message_history, previous_state)

    async def verify_state(
        self,
        latest_message: str,
        message_history: list[Message] | None = None,
        proposed_state: str | None = None,
        previous_state: str | None = None,
        transition_valid: bool = True,
        analysis_reasoning: str = "",
    ):
        del transition_valid
        return await intent_ai.verify_state(
            latest_message,
            message_history,
            proposed_state,
            previous_state,
            analysis_reasoning,
        )

    async def analyze_food_order_intent(
        self,
        latest_message: str,
        order_state: dict,
        message_history: list[Message] | None = None,
        previous_food_order_state: str | None = None,
    ):
        return await intent_ai.analyze_food_order_intent(
            latest_message,
            order_state,
            message_history,
            previous_food_order_state,
        )

    async def verify_food_order_state(
        self,
        latest_message: str,
        order_state: dict,
        message_history: list[Message] | None = None,
        proposed_state: str | None = None,
        previous_food_order_state: str | None = None,
        transition_valid: bool = True,
        analysis_reasoning: str = "",
    ):
        return await intent_ai.verify_food_order_state(
            latest_message,
            order_state,
            message_history,
            proposed_state,
            previous_food_order_state,
            transition_valid,
            analysis_reasoning,
        )

    async def handle_farewell(self, latest_message: str, message_history: list[Message] | None = None) -> str:
        return await visibility_ai.handle_farewell(latest_message, message_history)

    async def ask_clarifying_question(self, latest_message: str, message_history: list[Message] | None = None) -> str:
        return await visibility_ai.ask_clarifying_question(latest_message, message_history)

    async def handle_misc(self, latest_message: str, message_history: list[Message] | None = None) -> str:
        return await visibility_ai.handle_misc(latest_message, message_history)

    async def extract_order_items(self, latest_message: str, message_history: list[Message] | None = None):
        return await extraction_ai.extract_order_items(latest_message, message_history)

    async def extract_swap_items(self, latest_message: str, message_history: list[Message] | None = None):
        return await extraction_ai.extract_swap_items(latest_message, message_history)

    async def handle_unrecognized_state(self, latest_message: str, message_history: list[Message] | None = None) -> str:
        return await visibility_ai.handle_unrecognized_state(latest_message, message_history)

    async def resolve_confirmation(self, latest_message: str, message_history: list[Message] | None = None):
        return await extraction_ai.resolve_confirmation(latest_message, message_history)

    async def extract_add_items(
        self,
        latest_message: str,
        order_state: dict,
        message_history: list[Message] | None = None,
    ):
        return await extraction_ai.extract_add_items(latest_message, order_state, message_history)

    async def extract_modify_items(
        self,
        latest_message: str,
        order_state: dict,
        message_history: list[Message] | None = None,
    ):
        return await extraction_ai.extract_modify_items(latest_message, order_state, message_history)

    async def resolve_remove_item(self, latest_message: str, message_history: list[Message] | None = None):
        return await extraction_ai.resolve_remove_item(latest_message, message_history)

    async def answer_menu_question(
        self,
        latest_message: str,
        menu_context: str,
        message_history: list[Message] | None = None,
    ) -> str:
        return await visibility_ai.answer_menu_question(latest_message, menu_context, message_history)

    async def polish_food_order_reply(
        self,
        order_state: dict,
        order_outcome: dict,
        latest_message: str,
        message_history: list[Message] | None = None,
    ) -> str:
        return await cart_ai.polish_food_order_reply(order_state, order_outcome, latest_message, message_history)

    async def resolve_order_finalization(
        self,
        latest_message: str,
        order_state: dict,
        message_history: list[Message] | None = None,
    ):
        return await cart_ai.resolve_order_finalization(latest_message, order_state, message_history)

    async def supervise_order_state(
        self,
        proposed_order_state: dict,
        latest_message: str,
        message_history: list[Message] | None = None,
    ):
        return await cart_ai.supervise_order_state(proposed_order_state, latest_message, message_history)

    async def answer_restaurant_question(
        self,
        latest_message: str,
        restaurant_context: str,
        message_history: list[Message] | None = None,
    ) -> str:
        return await visibility_ai.answer_restaurant_question(latest_message, restaurant_context, message_history)
