import asyncio
import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from src.chatbot import gemini_client
from src.chatbot.exceptions import AIServiceError
from src.chatbot.internal_schemas import IntentAnalysis
from src.chatbot.schema import SwapItems


class _FakeAioModels:
    def __init__(self, response=None):
        self.calls = []
        self.response = response or SimpleNamespace(text='{"ok": true}', parsed={"ok": True})

    async def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class _FakeClient:
    def __init__(self, models=None):
        self.aio = SimpleNamespace(models=models or _FakeAioModels())


class _OkModel(BaseModel):
    ok: bool


def test_generate_model_enforces_json_schema(monkeypatch):
    fake_client = _FakeClient()
    monkeypatch.setattr(gemini_client, "_client", fake_client)

    result = asyncio.run(
        gemini_client.generate_model(
            [{"role": "system", "content": "Return JSON"}, {"role": "user", "content": "hi"}],
            _OkModel,
            temperature=0,
        )
    )

    assert result.ok is True
    config = fake_client.aio.models.calls[0]["config"]
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema == gemini_client.normalize_json_schema(_OkModel.model_json_schema())
    assert config.max_output_tokens is None


def test_generate_text_does_not_request_json(monkeypatch):
    models = _FakeAioModels(response=SimpleNamespace(text="short reply", parsed=None))
    fake_client = _FakeClient(models=models)
    monkeypatch.setattr(gemini_client, "_client", fake_client)

    response = asyncio.run(
        gemini_client.generate_text(
            [{"role": "system", "content": "Be brief"}, {"role": "user", "content": "hi"}],
            temperature=0.7,
        )
    )

    assert response == "short reply"
    config = models.calls[0]["config"]
    assert config.response_mime_type is None
    assert config.response_json_schema is None
    assert config.max_output_tokens is None


def test_generate_text_raises_on_empty_response(monkeypatch):
    fake_client = _FakeClient(models=_FakeAioModels(response=SimpleNamespace(text="", parsed=None)))
    monkeypatch.setattr(gemini_client, "_client", fake_client)

    with pytest.raises(AIServiceError, match="empty text content"):
        asyncio.run(
            gemini_client.generate_text(
                [{"role": "system", "content": "Be brief"}, {"role": "user", "content": "hi"}],
                temperature=0.7,
            )
        )


def test_generate_text_forwards_explicit_max_output_tokens(monkeypatch):
    models = _FakeAioModels(response=SimpleNamespace(text="short reply", parsed=None))
    fake_client = _FakeClient(models=models)
    monkeypatch.setattr(gemini_client, "_client", fake_client)

    response = asyncio.run(
        gemini_client.generate_text(
            [{"role": "system", "content": "Be brief"}, {"role": "user", "content": "hi"}],
            temperature=0.7,
            max_output_tokens=42,
        )
    )

    assert response == "short reply"
    assert models.calls[0]["config"].max_output_tokens == 42


def test_normalize_json_schema_inlines_refs_and_nullable_fields():
    normalized = gemini_client.normalize_json_schema(IntentAnalysis.model_json_schema())
    normalized_text = json.dumps(normalized)

    assert "$defs" not in normalized_text
    assert "$ref" not in normalized_text
    assert "anyOf" not in normalized_text
    assert "default" not in normalized_text
    assert "title" not in normalized_text
    assert normalized["properties"]["state"]["enum"] == [
        "greeting",
        "farewell",
        "vague_message",
        "restaurant_question",
        "menu_question",
        "food_order",
        "pickup_ping",
        "pickup_time_suggestion",
        "misc",
        "human_escalation",
        "order_complete",
        "order_review",
    ]
    assert normalized["properties"]["alternative"]["type"] == ["string", "null"]


def test_normalize_json_schema_handles_nested_models():
    normalized = gemini_client.normalize_json_schema(SwapItems.model_json_schema())
    remove_item = normalized["properties"]["remove"]["items"]

    assert remove_item["type"] == "object"
    assert remove_item["properties"]["modifier"]["type"] == ["string", "null"]
    assert remove_item["properties"]["selected_mods"]["type"] == ["object", "null"]
    assert remove_item["properties"]["selected_mods"]["additionalProperties"]["type"] == ["string", "array"]
    assert (
        remove_item["properties"]["selected_mods"]["additionalProperties"]["items"]["type"] == "string"
    )
    assert remove_item["properties"]["resolved_mods"]["type"] == ["array", "null"]


def test_generate_model_includes_raw_preview_on_non_json(monkeypatch):
    fake_client = _FakeClient(models=_FakeAioModels(response=SimpleNamespace(text="menu_question", parsed=None)))
    monkeypatch.setattr(gemini_client, "_client", fake_client)

    with pytest.raises(AIServiceError, match="Raw response preview: 'menu_question'"):
        asyncio.run(
            gemini_client.generate_model(
                [{"role": "system", "content": "Return JSON"}, {"role": "user", "content": "show me the menu"}],
                _OkModel,
                temperature=0,
            )
        )
