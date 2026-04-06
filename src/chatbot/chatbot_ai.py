import json

from openai import AsyncOpenAI, OpenAIError

from src.chatbot.exceptions import AIServiceError
from src.chatbot.internal_schemas import (
    FoodOrderIntentAnalysis,
    FoodOrderStateVerification,
    IntentAnalysis,
    OrderFinalizationIntent,
    OrderSupervisionResult,
    StateVerification,
)
from src.chatbot.prompts import (
    ANALYZE_FOOD_ORDER_INTENT_SYSTEM_PROMPT,
    ANALYZE_INTENT_SYSTEM_PROMPT,
    SUPERVISE_ORDER_STATE_SYSTEM_PROMPT,
    CLARIFY_VAGUE_MESSAGE_SYSTEM_PROMPT,
    EXTRACT_ADD_ITEMS_SYSTEM_PROMPT,
    EXTRACT_MODIFY_ITEMS_SYSTEM_PROMPT,
    EXTRACT_ORDER_ITEMS_SYSTEM_PROMPT,
    EXTRACT_SWAP_ITEMS_SYSTEM_PROMPT,
    FAREWELL_SYSTEM_PROMPT,
    MENU_QUESTION_SYSTEM_PROMPT,
    MISC_SYSTEM_PROMPT,
    POLISH_FOOD_ORDER_REPLY_SYSTEM_PROMPT,
    RESOLVE_CONFIRMATION_SYSTEM_PROMPT,
    RESOLVE_ORDER_FINALIZATION_SYSTEM_PROMPT,
    RESOLVE_REMOVE_ITEM_SYSTEM_PROMPT,
    RESTAURANT_QUESTION_SYSTEM_PROMPT,
    UNRECOGNIZED_STATE_SYSTEM_PROMPT,
    VERIFY_FOOD_ORDER_STATE_SYSTEM_PROMPT,
    VERIFY_STATE_SYSTEM_PROMPT,
)
from src.chatbot.schema import Message, ModifyItem, OrderItem, SwapItems
from src.config import settings

_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


class ChatbotAI:
    async def detectUserIntent(
        self,
        latest_message: str,
        message_history: list[Message] | None = None,
        previous_state: str | None = None,
    ) -> IntentAnalysis:
        history = [m.model_dump() for m in (message_history or [])[-10:]]
        messages: list[dict] = [{"role": "system", "content": ANALYZE_INTENT_SYSTEM_PROMPT}]
        if previous_state:
            messages.append({"role": "system", "content": f"Previous conversation state: {previous_state}"})
        messages.extend(history)
        messages.append({"role": "user", "content": latest_message})

        try:
            response = await _client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                max_tokens=200,
                temperature=0,
                response_format={"type": "json_object"},
            )
        except OpenAIError as e:
            raise AIServiceError(f"OpenAI request failed: {e}") from e

        try:
            raw = response.choices[0].message.content
            return IntentAnalysis(**json.loads(raw))
        except Exception as e:
            raise AIServiceError(f"Failed to parse intent analysis: {e}") from e

    async def verify_state(
        self,
        latest_message: str,
        message_history: list[Message] | None = None,
        proposed_state: str | None = None,
        previous_state: str | None = None,
        transition_valid: bool = True,
        analysis_reasoning: str = "",
    ) -> StateVerification:
        history = [m.model_dump() for m in (message_history or [])[-10:]]
        context_lines = [
            f"Proposed state: {proposed_state}",
            f"Previous state: {previous_state}",
            f"Transition valid: {transition_valid}",
            f"Original reasoning: {analysis_reasoning}",
        ]
        messages: list[dict] = [
            {"role": "system", "content": VERIFY_STATE_SYSTEM_PROMPT},
            {"role": "system", "content": "\n".join(context_lines)},
            *history,
            {"role": "user", "content": latest_message},
        ]

        try:
            response = await _client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                max_tokens=80,
                temperature=0,
                response_format={"type": "json_object"},
            )
        except OpenAIError as e:
            raise AIServiceError(f"OpenAI request failed: {e}") from e

        try:
            return StateVerification(**json.loads(response.choices[0].message.content))
        except Exception as e:
            raise AIServiceError(f"Failed to parse state verification: {e}") from e

    async def analyze_food_order_intent(
        self,
        latest_message: str,
        order_state: dict,
        message_history: list[Message] | None = None,
        previous_food_order_state: str | None = None,
    ) -> FoodOrderIntentAnalysis:
        history = [m.model_dump() for m in (message_history or [])[-6:]]
        messages: list[dict] = [{"role": "system", "content": ANALYZE_FOOD_ORDER_INTENT_SYSTEM_PROMPT}]
        context_lines = [f"Current order: {order_state}"]
        if previous_food_order_state:
            context_lines.append(f"Previous food order sub-state: {previous_food_order_state}")
        messages.append({"role": "system", "content": "\n".join(context_lines)})
        messages.extend(history)
        messages.append({"role": "user", "content": latest_message})

        try:
            response = await _client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                max_tokens=120,
                temperature=0,
                response_format={"type": "json_object"},
            )
        except OpenAIError as e:
            raise AIServiceError(f"OpenAI request failed: {e}") from e

        try:
            return FoodOrderIntentAnalysis(**json.loads(response.choices[0].message.content))
        except Exception as e:
            raise AIServiceError(f"Failed to parse food order intent analysis: {e}") from e

    async def verify_food_order_state(
        self,
        latest_message: str,
        order_state: dict,
        message_history: list[Message] | None = None,
        proposed_state: str | None = None,
        previous_food_order_state: str | None = None,
        transition_valid: bool = True,
        analysis_reasoning: str = "",
    ) -> FoodOrderStateVerification:
        history = [m.model_dump() for m in (message_history or [])[-6:]]
        context_lines = [
            f"Current order: {order_state}",
            f"Proposed sub-state: {proposed_state}",
            f"Previous sub-state: {previous_food_order_state}",
            f"Transition valid: {transition_valid}",
            f"Original reasoning: {analysis_reasoning}",
        ]
        messages: list[dict] = [
            {"role": "system", "content": VERIFY_FOOD_ORDER_STATE_SYSTEM_PROMPT},
            {"role": "system", "content": "\n".join(context_lines)},
            *history,
            {"role": "user", "content": latest_message},
        ]

        try:
            response = await _client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                max_tokens=80,
                temperature=0,
                response_format={"type": "json_object"},
            )
        except OpenAIError as e:
            raise AIServiceError(f"OpenAI request failed: {e}") from e

        try:
            return FoodOrderStateVerification(**json.loads(response.choices[0].message.content))
        except Exception as e:
            raise AIServiceError(f"Failed to parse food order state verification: {e}") from e

    async def handle_farewell(
        self,
        latest_message: str,
        message_history: list[Message] | None = None,
    ) -> str:
        history = [m.model_dump() for m in message_history] if message_history else []
        messages = [
            {"role": "system", "content": FAREWELL_SYSTEM_PROMPT},
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

    async def extract_swap_items(
        self,
        latest_message: str,
        message_history: list[Message] | None = None,
    ) -> SwapItems:
        history = [m.model_dump() for m in message_history] if message_history else []
        messages = [
            {"role": "system", "content": EXTRACT_SWAP_ITEMS_SYSTEM_PROMPT},
            *history,
            {"role": "user", "content": latest_message},
        ]

        try:
            response = await _client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                max_tokens=200,
                temperature=0,
                response_format={"type": "json_object"},
            )
        except OpenAIError as e:
            raise AIServiceError(f"OpenAI request failed: {e}") from e

        raw = json.loads(response.choices[0].message.content)
        return SwapItems(
            remove=[OrderItem(**item) for item in raw.get("remove", [])],
            add=[OrderItem(**item) for item in raw.get("add", [])],
        )

    async def handle_unrecognized_state(
        self,
        latest_message: str,
        message_history: list[Message] | None = None,
    ) -> str:
        history = [m.model_dump() for m in message_history] if message_history else []
        messages = [
            {"role": "system", "content": UNRECOGNIZED_STATE_SYSTEM_PROMPT},
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

    async def resolve_confirmation(
        self,
        latest_message: str,
        message_history: list[Message] | None = None,
    ) -> list[OrderItem]:
        history = [m.model_dump() for m in message_history] if message_history else []
        messages = [
            {"role": "system", "content": RESOLVE_CONFIRMATION_SYSTEM_PROMPT},
            *history,
            {"role": "user", "content": latest_message},
        ]

        try:
            response = await _client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                max_tokens=200,
                temperature=0,
                response_format={"type": "json_object"},
            )
        except OpenAIError as e:
            raise AIServiceError(f"OpenAI request failed: {e}") from e

        raw = json.loads(response.choices[0].message.content)
        return [OrderItem(**item) for item in raw.get("items", [])]

    async def extract_add_items(
        self,
        latest_message: str,
        order_state: dict,
        message_history: list[Message] | None = None,
    ) -> list[OrderItem]:
        history = [m.model_dump() for m in message_history] if message_history else []
        system = EXTRACT_ADD_ITEMS_SYSTEM_PROMPT.replace("{order_state}", str(order_state))
        messages = [
            {"role": "system", "content": system},
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

    async def extract_modify_items(
        self,
        latest_message: str,
        order_state: dict,
        message_history: list[Message] | None = None,
    ) -> list[ModifyItem]:
        history = [m.model_dump() for m in message_history] if message_history else []
        system = EXTRACT_MODIFY_ITEMS_SYSTEM_PROMPT.replace("{order_state}", str(order_state))
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
                temperature=0,
                response_format={"type": "json_object"},
            )
        except OpenAIError as e:
            raise AIServiceError(f"OpenAI request failed: {e}") from e

        raw = json.loads(response.choices[0].message.content)
        return [ModifyItem(**item) for item in raw.get("items", [])]

    async def resolve_remove_item(
        self,
        latest_message: str,
        message_history: list[Message] | None = None,
    ) -> list[OrderItem]:
        history = [m.model_dump() for m in message_history] if message_history else []
        messages = [
            {"role": "system", "content": RESOLVE_REMOVE_ITEM_SYSTEM_PROMPT},
            *history,
            {"role": "user", "content": latest_message},
        ]

        try:
            response = await _client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                max_tokens=200,
                temperature=0,
                response_format={"type": "json_object"},
            )
        except OpenAIError as e:
            raise AIServiceError(f"OpenAI request failed: {e}") from e

        raw = json.loads(response.choices[0].message.content)
        return [OrderItem(**item) for item in raw.get("items", [])]

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
                max_tokens=600,
                temperature=0.4,
            )
        except OpenAIError as e:
            raise AIServiceError(f"OpenAI request failed: {e}") from e

        return response.choices[0].message.content.strip()

    async def polish_food_order_reply(
        self,
        order_state: dict,
        latest_message: str,
        message_history: list[Message] | None = None,
    ) -> str:
        history = [m.model_dump() for m in (message_history or [])]
        system = POLISH_FOOD_ORDER_REPLY_SYSTEM_PROMPT.format(order_state=order_state)
        messages: list[dict] = [
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

    async def resolve_order_finalization(
        self,
        latest_message: str,
        order_state: dict,
        message_history: list[Message] | None = None,
    ) -> OrderFinalizationIntent:
        history = [m.model_dump() for m in (message_history or [])]
        system = RESOLVE_ORDER_FINALIZATION_SYSTEM_PROMPT.format(order_state=order_state)
        messages: list[dict] = [
            {"role": "system", "content": system},
            *history,
            {"role": "user", "content": latest_message},
        ]

        try:
            response = await _client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                max_tokens=40,
                temperature=0,
                response_format={"type": "json_object"},
            )
        except OpenAIError as e:
            raise AIServiceError(f"OpenAI request failed: {e}") from e

        try:
            return OrderFinalizationIntent(**json.loads(response.choices[0].message.content))
        except Exception as e:
            raise AIServiceError(f"Failed to parse order finalization intent: {e}") from e

    async def supervise_order_state(
        self,
        proposed_order_state: dict,
        latest_message: str,
        message_history: list[Message] | None = None,
    ) -> OrderSupervisionResult:
        history = [m.model_dump() for m in (message_history or [])]
        context_lines = [
            f"Proposed order state: {proposed_order_state}",
        ]
        messages: list[dict] = [
            {"role": "system", "content": SUPERVISE_ORDER_STATE_SYSTEM_PROMPT},
            {"role": "system", "content": "\n".join(context_lines)},
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

        try:
            return OrderSupervisionResult(**json.loads(response.choices[0].message.content))
        except Exception as e:
            raise AIServiceError(f"Failed to parse order supervision result: {e}") from e

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
