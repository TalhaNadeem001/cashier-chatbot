from __future__ import annotations

import json
from dataclasses import dataclass
from copy import deepcopy
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel

from src.chatbot.exceptions import AIServiceError
from src.chatbot.llm_messages import LLMMessage, split_system_instruction
from src.config import settings

_ModelT = TypeVar("_ModelT", bound=BaseModel)
_client: genai.Client | None = None
_SCHEMA_PREVIEW_LIMIT = 200


@dataclass(frozen=True, slots=True)
class GeminiFunctionTool:
    name: str
    description: str
    parameters_json_schema: dict[str, Any]
    handler: Callable[..., Awaitable[dict[str, Any]]]


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(
            vertexai=True,
            project=settings.GCP_PROJECT_ID,
            location=settings.GCP_LOCATION,
        )
    return _client


def _build_contents(
    messages: Sequence[LLMMessage],
) -> tuple[str | None, list[types.Content]]:
    system_instruction, conversational_messages = split_system_instruction(messages)
    contents: list[types.Content] = []
    for message in conversational_messages:
        role = "model" if message["role"] == "assistant" else "user"
        contents.append(
            types.Content(
                role=role,
                parts=[types.Part.from_text(text=message["content"])],
            )
        )
    return system_instruction, contents


def _merge_schema_types(current: object, extra: str) -> str | list[str]:
    if isinstance(current, list):
        values = [str(item) for item in current]
    elif isinstance(current, str):
        values = [current]
    else:
        values = []
    if extra not in values:
        values.append(extra)
    return values[0] if len(values) == 1 else values


def _schema_type_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        return [value]
    return []


def _resolve_json_ref(ref: str, defs: dict[str, object]) -> dict[str, object]:
    prefix = "#/$defs/"
    if not ref.startswith(prefix):
        raise ValueError(f"Unsupported JSON schema reference: {ref}")
    key = ref[len(prefix) :]
    target = defs.get(key)
    if not isinstance(target, dict):
        raise ValueError(f"Missing JSON schema definition for reference: {ref}")
    return deepcopy(target)


def _merge_any_of_variants(variants: list[dict[str, object]]) -> dict[str, object]:
    if not variants:
        return {}

    merged: dict[str, object] = {}
    types: list[str] = []

    for variant in variants:
        variant_types = _schema_type_list(variant.get("type"))
        if not variant_types:
            return deepcopy(variants[0])
        for variant_type in variant_types:
            if variant_type not in types:
                types.append(variant_type)
        for key, value in variant.items():
            if key == "type":
                continue
            if key not in merged:
                merged[key] = deepcopy(value)
                continue
            if merged[key] == value:
                continue
            if (
                key == "properties"
                and isinstance(merged[key], dict)
                and isinstance(value, dict)
            ):
                merged[key] = {**merged[key], **deepcopy(value)}
                continue
            if (
                key == "required"
                and isinstance(merged[key], list)
                and isinstance(value, list)
            ):
                merged[key] = list(dict.fromkeys([*merged[key], *value]))
                continue
            if (
                key == "enum"
                and isinstance(merged[key], list)
                and isinstance(value, list)
            ):
                merged[key] = list(dict.fromkeys([*merged[key], *value]))
                continue
            if key == "additionalProperties":
                if merged[key] is True or value is True:
                    merged[key] = True
                continue

    merged["type"] = types[0] if len(types) == 1 else types
    return merged


def _normalize_json_schema_node(
    node: object,
    *,
    defs: dict[str, object],
) -> object:
    if isinstance(node, list):
        return [
            normalized
            for item in node
            if (normalized := _normalize_json_schema_node(item, defs=defs)) is not None
        ]

    if not isinstance(node, dict):
        return node

    working = deepcopy(node)

    if "$ref" in working:
        resolved = _resolve_json_ref(str(working.pop("$ref")), defs)
        resolved.update(working)
        working = resolved

    any_of = working.pop("anyOf", None)
    if isinstance(any_of, list):
        normalized_variants = [
            variant
            for variant in (
                _normalize_json_schema_node(option, defs=defs) for option in any_of
            )
            if isinstance(variant, dict)
        ]
        if normalized_variants:
            working.update(_merge_any_of_variants(normalized_variants))

    normalized: dict[str, object] = {}
    allowed_keys = {
        "type",
        "properties",
        "required",
        "items",
        "prefixItems",
        "minItems",
        "maxItems",
        "additionalProperties",
        "enum",
        "format",
        "minimum",
        "maximum",
        "description",
    }

    for key, value in working.items():
        if key not in allowed_keys:
            continue
        if key == "properties" and isinstance(value, dict):
            normalized["properties"] = {
                str(prop_name): _normalize_json_schema_node(prop_schema, defs=defs)
                for prop_name, prop_schema in value.items()
            }
            continue
        if key == "items":
            normalized["items"] = _normalize_json_schema_node(value, defs=defs)
            continue
        if key == "prefixItems" and isinstance(value, list):
            normalized["prefixItems"] = _normalize_json_schema_node(value, defs=defs)
            continue
        if key == "additionalProperties" and isinstance(value, dict):
            normalized["additionalProperties"] = _normalize_json_schema_node(
                value, defs=defs
            )
            continue
        normalized[key] = value

    return normalized


def normalize_json_schema(schema: dict[str, object]) -> dict[str, object]:
    defs_raw = schema.get("$defs", {})
    defs = defs_raw if isinstance(defs_raw, dict) else {}
    normalized = _normalize_json_schema_node(schema, defs=defs)
    if not isinstance(normalized, dict):
        raise ValueError("Normalized schema must be an object schema.")
    return normalized


def _build_config(
    *,
    system_instruction: str | None,
    temperature: float,
    max_output_tokens: int | None = None,
    response_json_schema: dict | None = None,
    tools: list[types.Tool] | None = None,
    tool_config: types.ToolConfig | None = None,
    automatic_function_calling: types.AutomaticFunctionCallingConfig | None = None,
) -> types.GenerateContentConfig:
    config: dict[str, object] = {
        "temperature": temperature,
    }
    if max_output_tokens is not None:
        config["max_output_tokens"] = max_output_tokens
    if system_instruction:
        config["system_instruction"] = system_instruction
    if response_json_schema is not None:
        config["response_mime_type"] = "application/json"
        config["response_json_schema"] = response_json_schema
    if tools is not None:
        config["tools"] = tools
    if tool_config is not None:
        config["tool_config"] = tool_config
    if automatic_function_calling is not None:
        config["automatic_function_calling"] = automatic_function_calling
    return types.GenerateContentConfig(**config)


def _extract_text(response: object) -> str:
    text = getattr(response, "text", None)
    if text is None:
        raise AIServiceError("Gemini returned no text content.")
    text = str(text).strip()
    if not text:
        raise AIServiceError("Gemini returned empty text content.")
    return text


def _load_structured_payload(response: object) -> object:
    parsed = getattr(response, "parsed", None)
    if parsed is not None:
        return parsed
    return json.loads(_extract_text(response))


def _response_preview(response: object) -> str | None:
    text = getattr(response, "text", None)
    if text is None:
        return None
    preview = " ".join(str(text).split()).strip()
    if not preview:
        return None
    if len(preview) > _SCHEMA_PREVIEW_LIMIT:
        return f"{preview[:_SCHEMA_PREVIEW_LIMIT]}..."
    return preview


def _build_function_tools(
    function_tools: Sequence[GeminiFunctionTool],
) -> tuple[list[types.Tool], types.ToolConfig]:
    declarations = [
        types.FunctionDeclaration(
            name=tool.name,
            description=tool.description,
            parametersJsonSchema=tool.parameters_json_schema,
        )
        for tool in function_tools
    ]
    tools = [types.Tool(functionDeclarations=declarations)]
    tool_config = types.ToolConfig(
        functionCallingConfig=types.FunctionCallingConfig(
            mode=types.FunctionCallingConfigMode.AUTO,
        )
    )
    return tools, tool_config


def _extract_function_calls(response: object) -> list[types.FunctionCall]:
    direct_calls = getattr(response, "function_calls", None)
    if isinstance(direct_calls, list) and direct_calls:
        return [call for call in direct_calls if isinstance(call, types.FunctionCall)]

    function_calls: list[types.FunctionCall] = []
    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        if content is None:
            continue
        for part in getattr(content, "parts", []) or []:
            function_call = getattr(part, "function_call", None) or getattr(
                part, "functionCall", None
            )
            if isinstance(function_call, types.FunctionCall):
                function_calls.append(function_call)
    return function_calls


def _extract_response_contents(response: object) -> list[types.Content]:
    contents: list[types.Content] = []
    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        if isinstance(content, types.Content):
            contents.append(content)
    return contents


def _build_model_function_call_content(
    function_calls: Sequence[types.FunctionCall],
) -> types.Content:
    return types.Content(
        role="model",
        parts=[
            types.Part.from_function_call(
                name=str(call.name or ""), args=call.args or {}
            )
            for call in function_calls
            if call.name
        ],
    )


def _normalize_tool_response_payload(payload: object) -> dict[str, Any]:
    if isinstance(payload, BaseModel):
        return payload.model_dump(mode="json")
    if isinstance(payload, dict):
        return payload
    return {"result": payload}


async def _build_tool_response_content(
    function_calls: Sequence[types.FunctionCall],
    tool_handlers: dict[str, Callable[..., Awaitable[dict[str, Any]]]],
) -> types.Content:
    parts: list[types.Part] = []
    for function_call in function_calls:
        tool_name = str(function_call.name or "").strip()
        if not tool_name or tool_name not in tool_handlers:
            raise AIServiceError(
                f"Gemini requested unknown tool: {tool_name or '<missing>'}."
            )

        args = function_call.args or {}
        if not isinstance(args, dict):
            raise AIServiceError(
                f"Gemini returned invalid arguments for tool {tool_name!r}: {args!r}"
            )

        try:
            payload = await tool_handlers[tool_name](**args)
        except (
            Exception
        ) as exc:  # pragma: no cover - tool-specific failures are converted to payloads
            payload = {"success": False, "error": str(exc)}

        parts.append(
            types.Part.from_function_response(
                name=tool_name,
                response=_normalize_tool_response_payload(payload),
            )
        )

    return types.Content(role="tool", parts=parts)


def _debug_log_gemini_response(label: str, response: object) -> None:
    """Print SDK response metadata for local debugging (hangs, slow calls)."""
    cands = getattr(response, "candidates", None) or []
    n = len(cands) if isinstance(cands, list) else 0
    finish_reasons: list[str] = []
    if isinstance(cands, list):
        for i, cand in enumerate(cands):
            fr = getattr(cand, "finish_reason", None)
            finish_reasons.append(f"c{i}={fr!r}")
    usage = getattr(response, "usage_metadata", None)
    print(
        f"[Gemini] {label} response",
        f"type={type(response).__name__}",
        f"candidates={n}",
        f"finish_reasons=[{', '.join(finish_reasons)}]"
        if finish_reasons
        else "finish_reasons=[]",
        f"usage_metadata={usage!r}",
    )


async def generate_text(
    messages: Sequence[LLMMessage],
    *,
    temperature: float,
    max_output_tokens: int | None = None,
    model: str | None = None,
) -> str:
    system_instruction, contents = _build_contents(messages)
    resolved_model = model or settings.GEMINI_MODEL
    print(
        "[Gemini] generate_text",
        f"model={resolved_model!r}",
        f"content_turns={len(contents)}",
        f"system_inst_chars={len(system_instruction or '')}",
        f"temperature={temperature}",
        f"max_output_tokens={max_output_tokens!r}",
        "calling generate_content...",
    )
    try:
        response = await _get_client().aio.models.generate_content(
            model=resolved_model,
            contents=contents,
            config=_build_config(
                system_instruction=system_instruction,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
            ),
        )
    except Exception as e:  # pragma: no cover - provider exception types are SDK-owned
        print("[Gemini] generate_text generate_content raised:", repr(e))
        raise AIServiceError(f"Gemini request failed: {e}") from e
    _debug_log_gemini_response("generate_text", response)
    print("[Gemini] generate_text extracting text...")
    return _extract_text(response)


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

    system_instruction, contents = _build_contents(messages)
    tools, tool_config = _build_function_tools(function_tools)
    tool_handlers = {tool.name: tool.handler for tool in function_tools}
    current_contents: list[types.Content] = list(contents)

    resolved_model = model or settings.GEMINI_MODEL
    for tool_round in range(max_tool_calls + 1):
        print(
            "[Gemini] generate_text_with_tools",
            f"model={resolved_model!r}",
            f"tool_round={tool_round}",
            f"current_content_turns={len(current_contents)}",
            "calling generate_content...",
        )
        try:
            response = await _get_client().aio.models.generate_content(
                model=resolved_model,
                contents=current_contents,
                config=_build_config(
                    system_instruction=system_instruction,
                    temperature=temperature,
                    max_output_tokens=max_output_tokens,
                    tools=tools,
                    tool_config=tool_config,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(
                        disable=True
                    ),
                ),
            )
        except (
            Exception
        ) as e:  # pragma: no cover - provider exception types are SDK-owned
            print(
                "[Gemini] generate_text_with_tools generate_content raised:",
                repr(e),
            )
            raise AIServiceError(f"Gemini request failed: {e}") from e

        _debug_log_gemini_response(
            f"generate_text_with_tools round={tool_round}", response
        )
        function_calls = _extract_function_calls(response)
        if not function_calls:
            return _extract_text(response)

        if tool_round >= max_tool_calls:
            raise AIServiceError(
                f"Gemini exceeded the maximum number of tool calls ({max_tool_calls})."
            )

        response_contents = _extract_response_contents(response)
        if response_contents:
            current_contents.extend(response_contents)
        else:
            current_contents.append(_build_model_function_call_content(function_calls))

        current_contents.append(
            await _build_tool_response_content(function_calls, tool_handlers)
        )

    raise AIServiceError(
        f"Gemini exceeded the maximum number of tool calls ({max_tool_calls})."
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

    system_instruction, contents = _build_contents(messages)
    response_schema = normalize_json_schema(response_model.model_json_schema())
    tools, tool_config = _build_function_tools(function_tools)
    tool_handlers = {tool.name: tool.handler for tool in function_tools}
    current_contents: list[types.Content] = list(contents)

    resolved_model = model or settings.GEMINI_MODEL
    for tool_round in range(max_tool_calls + 1):
        print(
            "[Gemini] generate_model_with_tools",
            f"model={resolved_model!r}",
            f"tool_round={tool_round}",
            f"current_content_turns={len(current_contents)}",
            "calling generate_content...",
        )
        try:
            response = await _get_client().aio.models.generate_content(
                model=resolved_model,
                contents=current_contents,
                config=_build_config(
                    system_instruction=system_instruction,
                    temperature=temperature,
                    max_output_tokens=max_output_tokens,
                    response_json_schema=response_schema,
                    tools=tools,
                    tool_config=tool_config,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(
                        disable=True
                    ),
                ),
            )
        except (
            Exception
        ) as e:  # pragma: no cover - provider exception types are SDK-owned
            print(
                "[Gemini] generate_model_with_tools generate_content raised:",
                repr(e),
            )
            raise AIServiceError(f"Gemini request failed: {e}") from e

        _debug_log_gemini_response(
            f"generate_model_with_tools round={tool_round}", response
        )
        function_calls = _extract_function_calls(response)
        if not function_calls:
            try:
                payload = _load_structured_payload(response)
                if isinstance(payload, response_model):
                    return payload
                return response_model.model_validate(payload)
            except Exception as e:
                preview = _response_preview(response)
                if preview:
                    raise AIServiceError(
                        f"Failed to parse Gemini structured response: {e}. Raw response preview: {preview!r}"
                    ) from e
                raise AIServiceError(
                    f"Failed to parse Gemini structured response: {e}"
                ) from e

        if tool_round >= max_tool_calls:
            raise AIServiceError(
                f"Gemini exceeded the maximum number of tool calls ({max_tool_calls})."
            )

        response_contents = _extract_response_contents(response)
        if response_contents:
            current_contents.extend(response_contents)
        else:
            current_contents.append(_build_model_function_call_content(function_calls))

        current_contents.append(
            await _build_tool_response_content(function_calls, tool_handlers)
        )

    raise AIServiceError(
        f"Gemini exceeded the maximum number of tool calls ({max_tool_calls})."
    )


async def generate_model(
    messages: Sequence[LLMMessage],
    response_model: type[_ModelT],
    *,
    temperature: float,
    max_output_tokens: int | None = None,
    model: str | None = None,
) -> _ModelT:
    system_instruction, contents = _build_contents(messages)
    response_schema = normalize_json_schema(response_model.model_json_schema())
    resolved_model = model or settings.GEMINI_MODEL
    print(
        "[Gemini] generate_model",
        f"response_model={getattr(response_model, '__name__', response_model)!r}",
        f"model={resolved_model!r}",
        f"content_turns={len(contents)}",
        f"system_inst_chars={len(system_instruction or '')}",
        f"response_json_schema_chars={len(json.dumps(response_schema, default=str))}",
        f"temperature={temperature}",
        f"max_output_tokens={max_output_tokens!r}",
        "calling generate_content...",
    )
    try:
        response = await _get_client().aio.models.generate_content(
            model=resolved_model,
            contents=contents,
            config=_build_config(
                system_instruction=system_instruction,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                response_json_schema=response_schema,
            ),
        )
    except Exception as e:  # pragma: no cover - provider exception types are SDK-owned
        print("[Gemini] generate_model generate_content raised:", repr(e))
        raise AIServiceError(f"Gemini request failed: {e}") from e

    _debug_log_gemini_response("generate_model", response)
    print("[Gemini] generate_model loading structured payload / validate...")
    try:
        payload = _load_structured_payload(response)
        if isinstance(payload, response_model):
            out = payload
        else:
            out = response_model.model_validate(payload)
        print(
            "[Gemini] generate_model validate ok", f"result_type={type(out).__name__}"
        )
        return out
    except Exception as e:
        print("[Gemini] generate_model validate/payload failed:", repr(e))
        preview = _response_preview(response)
        if preview:
            raise AIServiceError(
                f"Failed to parse Gemini structured response: {e}. Raw response preview: {preview!r}"
            ) from e
        raise AIServiceError(f"Failed to parse Gemini structured response: {e}") from e
