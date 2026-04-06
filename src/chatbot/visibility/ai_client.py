from openai import AsyncOpenAI, OpenAIError

from src.chatbot.exceptions import AIServiceError
from src.chatbot.prompts import (
    CLARIFY_VAGUE_MESSAGE_SYSTEM_PROMPT,
    FAREWELL_SYSTEM_PROMPT,
    MENU_QUESTION_SYSTEM_PROMPT,
    MISC_SYSTEM_PROMPT,
    ORDER_COMPLETE_SYSTEM_PROMPT,
    ORDER_MODIFIER_REQUEST_SYSTEM_PROMPT,
    RESTAURANT_QUESTION_SYSTEM_PROMPT,
    UNRECOGNIZED_STATE_SYSTEM_PROMPT,
)
from src.chatbot.openai_messages import openai_chat_history_from_messages
from src.chatbot.schema import Message
from src.config import settings

_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


async def handle_farewell(
    latest_message: str,
    message_history: list[Message] | None = None,
) -> str:
    history = openai_chat_history_from_messages(message_history)
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
    latest_message: str,
    message_history: list[Message] | None = None,
) -> str:
    history = openai_chat_history_from_messages(message_history)
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
    latest_message: str,
    message_history: list[Message] | None = None,
) -> str:
    history = openai_chat_history_from_messages(message_history)
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


async def answer_menu_question(
    latest_message: str,
    menu_context: str,
    message_history: list[Message] | None = None,
) -> str:
    history = openai_chat_history_from_messages(message_history)
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


async def answer_restaurant_question(
    latest_message: str,
    restaurant_context: str,
    message_history: list[Message] | None = None,
) -> str:
    history = openai_chat_history_from_messages(message_history)
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


async def handle_order_complete(
    order_state: dict,
    latest_message: str,
    message_history: list[Message] | None = None,
) -> str:
    history = openai_chat_history_from_messages(message_history)
    system = ORDER_COMPLETE_SYSTEM_PROMPT.format(order_state=order_state)
    messages = [
        {"role": "system", "content": system},
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


async def handle_order_modifier_request(
    latest_message: str,
    order_state: dict,
    message_history: list[Message] | None = None,
) -> str:
    history = openai_chat_history_from_messages(message_history)
    system = ORDER_MODIFIER_REQUEST_SYSTEM_PROMPT.format(order_state=order_state)
    messages = [
        {"role": "system", "content": system},
        *history,
        {"role": "user", "content": latest_message},
    ]
    try:
        response = await _client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=60,
            temperature=0.7,
        )
    except OpenAIError as e:
        raise AIServiceError(f"OpenAI request failed: {e}") from e
    return response.choices[0].message.content.strip()


async def handle_unrecognized_state(
    latest_message: str,
    message_history: list[Message] | None = None,
) -> str:
    history = openai_chat_history_from_messages(message_history)
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
