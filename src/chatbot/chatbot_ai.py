from openai import AsyncOpenAI, OpenAIError
from src.config import settings
import json
from src.chatbot.constants import ConversationState, FoodOrderState
from src.chatbot.exceptions import AIServiceError, InvalidConversationStateError
from src.chatbot.prompts import CLARIFY_VAGUE_MESSAGE_SYSTEM_PROMPT, DETERMINE_FOOD_ORDER_STATE_SYSTEM_PROMPT, DETERMINE_STATE_SYSTEM_PROMPT, EXTRACT_ORDER_ITEMS_SYSTEM_PROMPT, MENU_QUESTION_SYSTEM_PROMPT, MISC_SYSTEM_PROMPT, RESTAURANT_QUESTION_SYSTEM_PROMPT
from src.chatbot.schema import Message, OrderItem

_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


class ChatbotAI:
    async def determine_conversation_state(
        self,
        latest_message: str,
        message_history: list[Message] | None = None,
    ) -> ConversationState:
        history = [m.model_dump() for m in message_history] if message_history else []
        messages = [
            {"role": "system", "content": DETERMINE_STATE_SYSTEM_PROMPT},
            *history,
            {"role": "user", "content": latest_message},
        ]

        try:
            response = await _client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                max_tokens=10,
                temperature=0,
            )
        except OpenAIError as e:
            raise AIServiceError(f"OpenAI request failed: {e}") from e

        raw = response.choices[0].message.content.strip().lower()

        try:
            return ConversationState(raw)
        except ValueError:
            raise InvalidConversationStateError(
                f"AI returned unrecognised state: '{raw}'"
            )

    async def ask_clarifying_question(
        self,
        latest_message: str,
        message_history: list[Message] | None = None,
    ) -> str:
        history = [m.model_dump() for m in message_history] if message_history else []
        messages = [
            {"role": "system", "content": CLARIFY_VAGUE_MESSAGE_SYSTEM_PROMPT},
            *history,
            {"role": "user", "content": latest_message},
        ]

        try:
            response = await _client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                max_tokens=80,
                temperature=0.7,
            )
        except OpenAIError as e:
            raise AIServiceError(f"OpenAI request failed: {e}") from e

        return response.choices[0].message.content.strip()

    async def handle_misc(
        self,
        latest_message: str,
        message_history: list[Message] | None = None,
    ) -> str:
        history = [m.model_dump() for m in message_history] if message_history else []
        messages = [
            {"role": "system", "content": MISC_SYSTEM_PROMPT},
            *history,
            {"role": "user", "content": latest_message},
        ]

        try:
            response = await _client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                max_tokens=100,
                temperature=0.7,
            )
        except OpenAIError as e:
            raise AIServiceError(f"OpenAI request failed: {e}") from e

        return response.choices[0].message.content.strip()

    async def extract_order_items(
        self,
        latest_message: str,
        message_history: list[Message] | None = None,
    ) -> list[OrderItem]:
        history = [m.model_dump() for m in message_history] if message_history else []
        messages = [
            {"role": "system", "content": EXTRACT_ORDER_ITEMS_SYSTEM_PROMPT},
            *history,
            {"role": "user", "content": latest_message},
        ]

        try:
            response = await _client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                max_tokens=300,
                temperature=0,
                response_format={"type": "json_object"},
            )
        except OpenAIError as e:
            raise AIServiceError(f"OpenAI request failed: {e}") from e

        raw = json.loads(response.choices[0].message.content)
        return [OrderItem(**item) for item in raw.get("items", [])]

    async def determine_food_order_state(
        self,
        latest_message: str,
        order_state: dict,
        message_history: list[Message] | None = None,
    ) -> FoodOrderState:
        history = [m.model_dump() for m in message_history] if message_history else []
        order_context = f"Current order: {order_state}"
        messages = [
            {"role": "system", "content": DETERMINE_FOOD_ORDER_STATE_SYSTEM_PROMPT},
            {"role": "system", "content": order_context},
            *history,
            {"role": "user", "content": latest_message},
        ]

        try:
            response = await _client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                max_tokens=10,
                temperature=0,
            )
        except OpenAIError as e:
            raise AIServiceError(f"OpenAI request failed: {e}") from e

        raw = response.choices[0].message.content.strip().lower()

        try:
            return FoodOrderState(raw)
        except ValueError:
            raise InvalidConversationStateError(
                f"AI returned unrecognised food order state: '{raw}'"
            )

    async def answer_menu_question(
        self,
        latest_message: str,
        menu_context: str,
        message_history: list[Message] | None = None,
    ) -> str:
        history = [m.model_dump() for m in message_history] if message_history else []
        system = MENU_QUESTION_SYSTEM_PROMPT.format(menu_context=menu_context)
        messages = [
            {"role": "system", "content": system},
            *history,
            {"role": "user", "content": latest_message},
        ]

        try:
            response = await _client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                max_tokens=200,
                temperature=0.4,
            )
        except OpenAIError as e:
            raise AIServiceError(f"OpenAI request failed: {e}") from e

        return response.choices[0].message.content.strip()

    async def answer_restaurant_question(
        self,
        latest_message: str,
        restaurant_context: str,
        message_history: list[Message] | None = None,
    ) -> str:
        history = [m.model_dump() for m in message_history] if message_history else []
        system = RESTAURANT_QUESTION_SYSTEM_PROMPT.format(restaurant_context=restaurant_context)
        messages = [
            {"role": "system", "content": system},
            *history,
            {"role": "user", "content": latest_message},
        ]

        try:
            response = await _client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                max_tokens=200,
                temperature=0.4,
            )
        except OpenAIError as e:
            raise AIServiceError(f"OpenAI request failed: {e}") from e

        return response.choices[0].message.content.strip()
