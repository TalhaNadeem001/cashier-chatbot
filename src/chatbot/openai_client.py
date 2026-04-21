from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel

from src.chatbot.constants import _PARSE_VALIDATION_ERROR_PREFIX
from src.chatbot.exceptions import AIServiceError
from src.chatbot.llm_tools import GeminiFunctionTool
from src.chatbot.llm_messages import LLMMessage, split_system_instruction
from src.config import settings

_ModelT = TypeVar("_ModelT", bound=BaseModel)
_oai: AsyncOpenAI | None = None
_SCHEMA_PREVIEW_LIMIT = 200


def _get_openai_client() -> AsyncOpenAI:
    global _oai
    if _oai is None:
        key = settings.OPENAI_API_KEY
        if not key:
            raise AIServiceError(
                "OPENAI_API_KEY is required when LLM_PROVIDER is openai"
            )
        _oai = AsyncOpenAI(api_key=key)
    return _oai


def _normalize_tool_response_payload(payload: object) -> dict[str, Any]:
    if isinstance(payload, BaseModel):
        return payload.model_dump(mode="json")
    if isinstance(payload, dict):
        return payload
    return {"result": payload}


def _openai_chat_messages(messages: Sequence[LLMMessage]) -> list[dict[str, Any]]:
    system_instruction, conversational = split_system_instruction(messages)
    out: list[dict[str, Any]] = []
    if system_instruction:
        out.append({"role": "system", "content": system_instruction})
    for msg in conversational:
        role = msg["role"]
        if role == "assistant":
            out.append({"role": "assistant", "content": msg["content"]})
        else:
            out.append({"role": "user", "content": msg["content"]})
    return out


def _response_preview_from_text(text: str | None) -> str | None:
    if text is None:
        return None
    preview = " ".join(str(text).split()).strip()
    if not preview:
        return None
    if len(preview) > _SCHEMA_PREVIEW_LIMIT:
        return f"{preview[:_SCHEMA_PREVIEW_LIMIT]}..."
    return preview


def _parse_json_from_assistant_text(text: str) -> object:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines)
    return json.loads(stripped)


def _build_openai_tools(
    function_tools: Sequence[GeminiFunctionTool],
) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters_json_schema,
            },
        }
        for tool in function_tools
    ]


async def generate_text(
    messages: Sequence[LLMMessage],
    *,
    temperature: float,
    max_output_tokens: int | None = None,
    model: str | None = None,
) -> str:
    resolved_model = model or settings.OPENAI_MODEL
    api_messages = _openai_chat_messages(messages)
    print(
        "[OpenAI] generate_text",
        f"model={resolved_model!r}",
        f"messages={len(api_messages)}",
        f"temperature={temperature}",
        f"max_output_tokens={max_output_tokens!r}",
    )
    client = _get_openai_client()
    kwargs: dict[str, Any] = {
        "model": resolved_model,
        "messages": api_messages,
        "temperature": temperature,
    }
    if max_output_tokens is not None:
        kwargs["max_completion_tokens"] = max_output_tokens
    try:
        response = await client.chat.completions.create(**kwargs)
    except Exception as e:  # pragma: no cover - provider exception types are SDK-owned
        print("[OpenAI] generate_text create raised:", repr(e))
        raise AIServiceError(f"OpenAI request failed: {e}") from e
    text = response.choices[0].message.content
    if text is None:
        raise AIServiceError("OpenAI returned no text content.")
    text = str(text).strip()
    if not text:
        raise AIServiceError("OpenAI returned empty text content.")
    return text


async def generate_model(
    messages: Sequence[LLMMessage],
    response_model: type[_ModelT],
    *,
    temperature: float,
    max_output_tokens: int | None = None,
    model: str | None = None,
) -> _ModelT:
    resolved_model = model or settings.OPENAI_MODEL
    api_messages = _openai_chat_messages(messages)
    print(
        "[OpenAI] generate_model",
        f"response_model={getattr(response_model, '__name__', response_model)!r}",
        f"model={resolved_model!r}",
        f"messages={len(api_messages)}",
        f"temperature={temperature}",
        f"max_output_tokens={max_output_tokens!r}",
    )
    client = _get_openai_client()
    kwargs: dict[str, Any] = {
        "model": resolved_model,
        "messages": api_messages,
        "response_format": response_model,
        "temperature": temperature,
    }
    if max_output_tokens is not None:
        kwargs["max_completion_tokens"] = max_output_tokens
    try:
        completion = await client.chat.completions.parse(**kwargs)
    except Exception as e:  # pragma: no cover - provider exception types are SDK-owned
        print("[OpenAI] generate_model parse raised:", repr(e))
        raise AIServiceError(f"OpenAI request failed: {e}") from e
    parsed = completion.choices[0].message.parsed
    if parsed is not None:
        return parsed
    raw = completion.choices[0].message.content
    try:
        if raw:
            payload = _parse_json_from_assistant_text(raw)
            if isinstance(payload, response_model):
                return payload
            return response_model.model_validate(payload)
    except Exception as e:
        preview = _response_preview_from_text(raw)
        if preview:
            raise AIServiceError(
                f"{_PARSE_VALIDATION_ERROR_PREFIX} {e}. Raw response preview: {preview!r}"
            ) from e
        raise AIServiceError(f"{_PARSE_VALIDATION_ERROR_PREFIX} {e}") from e
    raise AIServiceError("OpenAI returned no parsed structured content.")


async def generate_text_with_tools(
    messages: Sequence[LLMMessage],
    *,
    function_tools: Sequence[GeminiFunctionTool],
    temperature: float,
    max_tool_calls: int,
    max_output_tokens: int | None = None,
    model: str | None = None,
) -> str:
    if not function_tools:
        return await generate_text(
            messages,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            model=model,
        )

    resolved_model = model or settings.OPENAI_MODEL
    tool_handlers = {tool.name: tool.handler for tool in function_tools}
    tools = _build_openai_tools(function_tools)
    api_messages = _openai_chat_messages(messages)

    client = _get_openai_client()
    for tool_round in range(max_tool_calls + 1):
        print(
            "[OpenAI] generate_text_with_tools",
            f"model={resolved_model!r}",
            f"tool_round={tool_round}",
            f"messages={len(api_messages)}",
        )
        kwargs: dict[str, Any] = {
            "model": resolved_model,
            "messages": api_messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": temperature,
        }
        if max_output_tokens is not None:
            kwargs["max_completion_tokens"] = max_output_tokens
        try:
            response = await client.chat.completions.create(**kwargs)
        except Exception as e:  # pragma: no cover
            print("[OpenAI] generate_text_with_tools create raised:", repr(e))
            raise AIServiceError(f"OpenAI request failed: {e}") from e

        msg = response.choices[0].message
        if not msg.tool_calls:
            content = msg.content
            if content is None:
                raise AIServiceError("OpenAI returned no text content.")
            text = str(content).strip()
            if not text:
                raise AIServiceError("OpenAI returned empty text content.")
            return text

        if tool_round >= max_tool_calls:
            raise AIServiceError(
                f"OpenAI exceeded the maximum number of tool calls ({max_tool_calls})."
            )

        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": msg.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments or "{}",
                    },
                }
                for tc in msg.tool_calls
            ],
        }
        api_messages.append(assistant_msg)

        for tc in msg.tool_calls:
            tool_name = str(tc.function.name or "").strip()
            if not tool_name or tool_name not in tool_handlers:
                raise AIServiceError(
                    f"OpenAI requested unknown tool: {tool_name or '<missing>'}."
                )
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError as e:
                raise AIServiceError(
                    f"OpenAI returned invalid arguments for tool {tool_name!r}: {tc.function.arguments!r}"
                ) from e
            if not isinstance(args, dict):
                raise AIServiceError(
                    f"OpenAI returned invalid arguments for tool {tool_name!r}: {args!r}"
                )
            try:
                payload = await tool_handlers[tool_name](**args)
            except Exception as exc:  # pragma: no cover
                payload = {"success": False, "error": str(exc)}
            api_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(_normalize_tool_response_payload(payload)),
                }
            )

    raise AIServiceError(
        f"OpenAI exceeded the maximum number of tool calls ({max_tool_calls})."
    )


async def generate_model_with_tools(
    messages: Sequence[LLMMessage],
    response_model: type[_ModelT],
    *,
    function_tools: Sequence[GeminiFunctionTool],
    temperature: float,
    max_tool_calls: int,
    max_output_tokens: int | None = None,
    model: str | None = None,
) -> _ModelT:
    if not function_tools:
        return await generate_model(
            messages,
            response_model,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            model=model,
        )

    resolved_model = model or settings.OPENAI_MODEL
    tool_handlers = {tool.name: tool.handler for tool in function_tools}
    tools = _build_openai_tools(function_tools)
    api_messages = _openai_chat_messages(messages)

    client = _get_openai_client()
    for tool_round in range(max_tool_calls + 1):
        print(
            "[OpenAI] generate_model_with_tools",
            f"model={resolved_model!r}",
            f"tool_round={tool_round}",
            f"messages={len(api_messages)}",
        )
        kwargs: dict[str, Any] = {
            "model": resolved_model,
            "messages": api_messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": temperature,
        }
        if max_output_tokens is not None:
            kwargs["max_completion_tokens"] = max_output_tokens
        try:
            response = await client.chat.completions.create(**kwargs)
        except Exception as e:  # pragma: no cover
            print("[OpenAI] generate_model_with_tools create raised:", repr(e))
            raise AIServiceError(f"OpenAI request failed: {e}") from e

        msg = response.choices[0].message
        if not msg.tool_calls:
            raw = msg.content
            if raw is None:
                raise AIServiceError("OpenAI returned no structured content.")
            try:
                payload = _parse_json_from_assistant_text(str(raw))
                if isinstance(payload, response_model):
                    return payload
                return response_model.model_validate(payload)
            except Exception as e:
                preview = _response_preview_from_text(str(raw) if raw else None)
                if preview:
                    raise AIServiceError(
                        f"{_PARSE_VALIDATION_ERROR_PREFIX} {e}. Raw response preview: {preview!r}"
                    ) from e
                raise AIServiceError(f"{_PARSE_VALIDATION_ERROR_PREFIX} {e}") from e

        if tool_round >= max_tool_calls:
            raise AIServiceError(
                f"OpenAI exceeded the maximum number of tool calls ({max_tool_calls})."
            )

        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": msg.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments or "{}",
                    },
                }
                for tc in msg.tool_calls
            ],
        }
        api_messages.append(assistant_msg)

        for tc in msg.tool_calls:
            tool_name = str(tc.function.name or "").strip()
            if not tool_name or tool_name not in tool_handlers:
                raise AIServiceError(
                    f"OpenAI requested unknown tool: {tool_name or '<missing>'}."
                )
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError as e:
                raise AIServiceError(
                    f"OpenAI returned invalid arguments for tool {tool_name!r}: {tc.function.arguments!r}"
                ) from e
            if not isinstance(args, dict):
                raise AIServiceError(
                    f"OpenAI returned invalid arguments for tool {tool_name!r}: {args!r}"
                )
            try:
                payload = await tool_handlers[tool_name](**args)
            except Exception as exc:  # pragma: no cover
                payload = {"success": False, "error": str(exc)}
            api_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(_normalize_tool_response_payload(payload)),
                }
            )

    raise AIServiceError(
        f"OpenAI exceeded the maximum number of tool calls ({max_tool_calls})."
    )
