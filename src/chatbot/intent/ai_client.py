import json

from openai import AsyncOpenAI, OpenAIError

from src.chatbot.exceptions import AIServiceError
from src.chatbot.internal_schemas import (
    CustomerNameAnalysis,
    FoodOrderIntentAnalysis,
    FoodOrderStateVerification,
    IntentAnalysis,
    ModifierJourneyAnalysis,
    ModifierStateIntentAnalysis,
    ModifierStateVerification,
    StateVerification,
)
from src.chatbot.intent.prompts import (
    ANALYZE_FOOD_ORDER_INTENT_SYSTEM_PROMPT,
    ANALYZE_INTENT_SYSTEM_PROMPT,
    ANALYZE_MODIFIER_JOURNEY_INTENT_SYSTEM_PROMPT,
    ANALYZE_MODIFIER_STATE_INTENT_SYSTEM_PROMPT,
    GET_CUSTOMER_NAME_SYSTEM_PROMPT,
    VERIFY_FOOD_ORDER_STATE_SYSTEM_PROMPT,
    VERIFY_MODIFIER_STATE_SYSTEM_PROMPT,
    VERIFY_STATE_SYSTEM_PROMPT,
)
from src.chatbot.schema import Message
from src.config import settings

_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


async def detect_user_intent(
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
    latest_message: str,
    message_history: list[Message] | None = None,
    proposed_state: str | None = None,
    previous_state: str | None = None,
    analysis_reasoning: str = "",
) -> StateVerification:
    history = [m.model_dump() for m in (message_history or [])[-10:]]
    context_lines = [
        f"Proposed state: {proposed_state}",
        f"Previous state: {previous_state}",
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


async def analyze_modifier_state_intent(
    latest_message: str,
    order_state: dict,
    message_history: list[Message] | None = None,
) -> ModifierStateIntentAnalysis:
    history = [m.model_dump() for m in (message_history or [])[-6:]]
    messages: list[dict] = [{"role": "system", "content": ANALYZE_MODIFIER_STATE_INTENT_SYSTEM_PROMPT}]
    messages.append({"role": "system", "content": f"Current order: {order_state}"})
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
        return ModifierStateIntentAnalysis(**json.loads(response.choices[0].message.content))
    except Exception as e:
        raise AIServiceError(f"Failed to parse modifier state intent analysis: {e}") from e


async def verify_modifier_state(
    latest_message: str,
    order_state: dict,
    message_history: list[Message] | None = None,
    proposed_state: str | None = None,
    analysis_reasoning: str = "",
) -> ModifierStateVerification:
    history = [m.model_dump() for m in (message_history or [])[-6:]]
    context_lines = [
        f"Current order: {order_state}",
        f"Proposed modifier sub-state: {proposed_state}",
        f"Original reasoning: {analysis_reasoning}",
    ]
    messages: list[dict] = [
        {"role": "system", "content": VERIFY_MODIFIER_STATE_SYSTEM_PROMPT},
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
        return ModifierStateVerification(**json.loads(response.choices[0].message.content))
    except Exception as e:
        raise AIServiceError(f"Failed to parse modifier state verification: {e}") from e


async def analyze_food_order_intent(
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


async def get_customer_name(
    message_history: list[Message] | None,
    latest_message: str,
) -> CustomerNameAnalysis:
    history = [m.model_dump() for m in (message_history or [])[-10:]]
    messages: list[dict] = [
        {"role": "system", "content": GET_CUSTOMER_NAME_SYSTEM_PROMPT},
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
        return CustomerNameAnalysis(**json.loads(response.choices[0].message.content))
    except Exception as e:
        raise AIServiceError(f"Failed to parse customer name analysis: {e}") from e


async def analyze_modifier_journey_intent(
    latest_message: str,
    item_name: str,
    missing_mod_groups_text: str,
) -> ModifierJourneyAnalysis:
    context = f"Item being customized: {item_name}\nMissing modifier groups:\n{missing_mod_groups_text}"
    messages: list[dict] = [
        {"role": "system", "content": ANALYZE_MODIFIER_JOURNEY_INTENT_SYSTEM_PROMPT},
        {"role": "system", "content": context},
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
        return ModifierJourneyAnalysis(**json.loads(response.choices[0].message.content))
    except Exception as e:
        raise AIServiceError(f"Failed to parse modifier journey intent: {e}") from e
