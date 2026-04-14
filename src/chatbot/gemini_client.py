from __future__ import annotations

import json
from copy import deepcopy
from collections.abc import Sequence
from typing import TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel

from src.chatbot.exceptions import AIServiceError
from src.chatbot.llm_messages import LLMMessage, split_system_instruction
from src.config import settings

_ModelT = TypeVar("_ModelT", bound=BaseModel)
_client: genai.Client | None = None
_SCHEMA_PREVIEW_LIMIT = 200


def _resolve_api_key() -> str:
    api_key = settings.GEMINI_API_KEY or settings.OPENAI_API_KEY
    if api_key:
        return api_key
    raise AIServiceError("Gemini API key is not configured. Set GEMINI_API_KEY or OPENAI_API_KEY.")


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=_resolve_api_key())
    return _client


def _build_contents(messages: Sequence[LLMMessage]) -> tuple[str | None, list[types.Content]]:
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
    key = ref[len(prefix):]
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
            if key == "properties" and isinstance(merged[key], dict) and isinstance(value, dict):
                merged[key] = {**merged[key], **deepcopy(value)}
                continue
            if key == "required" and isinstance(merged[key], list) and isinstance(value, list):
                merged[key] = list(dict.fromkeys([*merged[key], *value]))
                continue
            if key == "enum" and isinstance(merged[key], list) and isinstance(value, list):
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
                _normalize_json_schema_node(option, defs=defs)
                for option in any_of
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
            normalized["additionalProperties"] = _normalize_json_schema_node(value, defs=defs)
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


async def generate_text(
    messages: Sequence[LLMMessage],
    *,
    temperature: float,
    max_output_tokens: int | None = None,
    model: str | None = None,
) -> str:
    system_instruction, contents = _build_contents(messages)
    try:
        response = await _get_client().aio.models.generate_content(
            model=model or settings.GEMINI_MODEL,
            contents=contents,
            config=_build_config(
                system_instruction=system_instruction,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
            ),
        )
    except Exception as e:  # pragma: no cover - provider exception types are SDK-owned
        raise AIServiceError(f"Gemini request failed: {e}") from e
    return _extract_text(response)


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
    try:
        response = await _get_client().aio.models.generate_content(
            model=model or settings.GEMINI_MODEL,
            contents=contents,
            config=_build_config(
                system_instruction=system_instruction,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                response_json_schema=response_schema,
            ),
        )
    except Exception as e:  # pragma: no cover - provider exception types are SDK-owned
        raise AIServiceError(f"Gemini request failed: {e}") from e

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
        raise AIServiceError(f"Failed to parse Gemini structured response: {e}") from e
