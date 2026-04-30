import asyncio

import pytest

from src.chatbot import gemini_client
from src.chatbot import llm_client
from src.chatbot import orchestrator as orchestrator_mod
from src.chatbot.exceptions import AIServiceError
from src.chatbot.gemini_client import GeminiFunctionTool
from src.chatbot.orchestrator import ExecutionAgent
from src.chatbot.schema import (
    CurrentOrderDetails,
    CurrentOrderLineItem,
    ParsedRequestConfidenceLevel,
    ParsedRequestIntent,
    ParsedRequestItem,
    ParsedRequestItems,
    ParsedRequestsPayload,
    PreparedExecutionContext,
)


def _context_object(
    *,
    latest_customer_message: str = "add a burger",
    line_items: list[CurrentOrderLineItem] | None = None,
) -> PreparedExecutionContext:
    return PreparedExecutionContext(
        session_id="session-1",
        merchant_id="merchant-1",
        latest_customer_message=latest_customer_message,
        current_order_details=CurrentOrderDetails(
            order_id="order-1",
            line_items=line_items or [],
            order_total=0,
            raw_error=None,
        ),
        latest_k_messages_by_customer=["hello"],
        clover_creds=None,
        clover_error=None,
    )


def _request(
    *,
    intent: ParsedRequestIntent,
    confidence: ParsedRequestConfidenceLevel = ParsedRequestConfidenceLevel.HIGH,
    name: str = "burger",
    quantity: int = 1,
    details: str = "",
    request_details: str = "test request",
) -> ParsedRequestsPayload:
    return ParsedRequestsPayload(
        data=[
            ParsedRequestItem(
                intent=intent,
                confidence_level=confidence,
                request_items=ParsedRequestItems(
                    name=name,
                    quantity=quantity,
                    details=details,
                ),
                request_details=request_details,
            )
        ]
    )


def _tool(name: str, handler, description: str = "tool") -> GeminiFunctionTool:
    return GeminiFunctionTool(
        name=name,
        description=description,
        parameters_json_schema={"type": "object", "properties": {}},
        handler=handler,
    )


def _mock_save_clarification(monkeypatch):
    monkeypatch.setattr(
        orchestrator_mod,
        "saveClarificationAndIntent",
        lambda session_id, clarification_questions, parsed_intents, **kwargs: __import__(
            "asyncio"
        ).sleep(0),
    )


def _mock_get_clarification(monkeypatch):
    async def fake_get(session_id):
        return {
            "success": False,
            "clarification_questions": "",
            "parsed_intents": [],
            "saved_at": None,
            "error": "not found",
        }

    monkeypatch.setattr(orchestrator_mod, "getClarificationAndIntent", fake_get)


def test_execution_agent_adds_item_and_records_tracker(monkeypatch):
    """LLM calls addItemsToOrder; tracker records the action."""

    async def fake_generate(
        messages, *, function_tools, temperature, max_tool_calls, **kwargs
    ):
        handlers = {t.name: t.handler for t in function_tools}
        await handlers["addItemsToOrder"](items=[{"itemId": "item-1", "quantity": 1}])
        return "Added 1 x Burger."

    monkeypatch.setattr(llm_client, "generate_text_with_tools", fake_generate)
    _mock_save_clarification(monkeypatch)
    _mock_get_clarification(monkeypatch)

    # Patch addItemsToOrder in orchestrator module so it returns a known result
    async def fake_add(session_id, items, creds=None):
        return {
            "success": True,
            "addedItems": [{"name": "Burger", "quantity": 1}],
            "failedItems": [],
            "updatedOrderTotal": 1099,
        }

    monkeypatch.setattr(orchestrator_mod, "addItemsToOrder", fake_add)

    agent = ExecutionAgent(system_prompt="")
    result = asyncio.run(
        agent.run(
            parsed_requests=_request(intent=ParsedRequestIntent.ADD_ITEM),
            context_object=_context_object(),
        )
    )

    assert result.agent_reply == "Added 1 x Burger."
    assert result.session_id == "session-1"
    assert result.actions_executed == ["added 1x Burger"]
    assert result.order_updated is True


def test_execution_agent_returns_pending_clarification_for_low_confidence(monkeypatch):
    """Low-confidence items are extracted from parsed_requests before the LLM call."""

    async def fake_generate(
        messages, *, function_tools, temperature, max_tool_calls, **kwargs
    ):
        return "Can you clarify that request?"

    monkeypatch.setattr(llm_client, "generate_text_with_tools", fake_generate)
    _mock_save_clarification(monkeypatch)
    _mock_get_clarification(monkeypatch)

    agent = ExecutionAgent(system_prompt="")
    result = asyncio.run(
        agent.run(
            parsed_requests=_request(
                intent=ParsedRequestIntent.ADD_ITEM,
                confidence=ParsedRequestConfidenceLevel.LOW,
                request_details="maybe the spicy thing",
            ),
            context_object=_context_object(
                latest_customer_message="maybe the spicy thing"
            ),
        )
    )

    assert "Can you clarify that request?" in result.agent_reply
    assert result.pending_clarifications == ["maybe the spicy thing"]
    assert result.order_updated is False


def test_execution_agent_replaces_item_and_records_tracker(monkeypatch):
    """LLM calls replaceItemInOrder; tracker records replaced action."""

    async def fake_generate(
        messages, *, function_tools, temperature, max_tool_calls, **kwargs
    ):
        handlers = {t.name: t.handler for t in function_tools}
        await handlers["replaceItemInOrder"](
            replacement={"itemId": "item-rings", "quantity": 1},
            itemName="fries",
        )
        return "Replaced Regular Fries with Onion Rings."

    monkeypatch.setattr(llm_client, "generate_text_with_tools", fake_generate)

    async def fake_replace(
        session_id, replacement, *, lineItemId=None, orderPosition=None, itemName=None, creds=None
    ):
        return {
            "success": True,
            "removedItem": {"name": "Regular Fries", "quantity": 1},
            "addedItem": {
                "name": "Onion Rings",
                "quantity": 1,
                "modifiersApplied": [],
                "lineTotal": 399,
            },
            "updatedOrderTotal": 399,
            "error": None,
        }

    monkeypatch.setattr(orchestrator_mod, "replaceItemInOrder", fake_replace)
    _mock_save_clarification(monkeypatch)
    _mock_get_clarification(monkeypatch)

    agent = ExecutionAgent(system_prompt="")
    result = asyncio.run(
        agent.run(
            parsed_requests=_request(
                intent=ParsedRequestIntent.REPLACE_ITEM,
                name="fries",
                details="onion rings",
                request_details="swap fries for onion rings",
            ),
            context_object=_context_object(
                latest_customer_message="swap fries for onion rings"
            ),
        )
    )

    assert result.agent_reply == "Replaced Regular Fries with Onion Rings."
    assert result.actions_executed == ["replaced Regular Fries with Onion Rings"]
    assert result.order_updated is True


def test_execution_agent_removes_item_and_records_tracker(monkeypatch):
    """LLM calls removeItemFromOrder; tracker records removed action."""

    async def fake_generate(
        messages, *, function_tools, temperature, max_tool_calls, **kwargs
    ):
        handlers = {t.name: t.handler for t in function_tools}
        await handlers["removeItemFromOrder"](target={"itemName": "Fries"})
        return "Removed Fries."

    monkeypatch.setattr(llm_client, "generate_text_with_tools", fake_generate)

    async def fake_remove(session_id, target, creds=None):
        return {
            "success": True,
            "removedItem": {"name": "Fries", "quantity": 1},
            "error": None,
        }

    monkeypatch.setattr(orchestrator_mod, "removeItemFromOrder", fake_remove)
    _mock_save_clarification(monkeypatch)
    _mock_get_clarification(monkeypatch)

    agent = ExecutionAgent(system_prompt="")
    result = asyncio.run(
        agent.run(
            parsed_requests=_request(
                intent=ParsedRequestIntent.REMOVE_ITEM,
                name="fries",
                request_details="remove fries",
            ),
            context_object=_context_object(),
        )
    )

    assert result.agent_reply == "Removed Fries."
    assert result.actions_executed == ["removed Fries"]
    assert result.order_updated is True


def test_execution_agent_confirm_order_records_tracker(monkeypatch):
    """LLM calls confirmOrder; tracker records confirmed order."""

    async def fake_generate(
        messages, *, function_tools, temperature, max_tool_calls, **kwargs
    ):
        handlers = {t.name: t.handler for t in function_tools}
        await handlers["calcOrderPrice"]()
        await handlers["confirmOrder"]()
        return "Your order is confirmed."

    monkeypatch.setattr(llm_client, "generate_text_with_tools", fake_generate)

    async def fake_calc(session_id, creds=None):
        return {"success": True, "total": 1165, "error": None}

    async def fake_confirm(session_id, creds=None):
        return {
            "success": True,
            "orderId": "order-1",
            "finalTotal": 1165,
            "error": None,
        }

    monkeypatch.setattr(orchestrator_mod, "calcOrderPrice", fake_calc)
    monkeypatch.setattr(orchestrator_mod, "confirmOrder", fake_confirm)
    _mock_save_clarification(monkeypatch)
    _mock_get_clarification(monkeypatch)

    agent = ExecutionAgent(system_prompt="")
    result = asyncio.run(
        agent.run(
            parsed_requests=_request(
                intent=ParsedRequestIntent.CONFIRM_ORDER,
                name="",
                quantity=0,
                request_details="yes",
            ),
            context_object=_context_object(
                latest_customer_message="yes",
                line_items=[
                    CurrentOrderLineItem(
                        line_item_id="1", name="Burger", quantity=1, price=1099
                    )
                ],
            ),
        )
    )

    assert result.agent_reply == "Your order is confirmed."
    assert result.actions_executed == ["confirmed order"]
    assert result.order_updated is True


def test_execution_agent_cancel_order_records_tracker(monkeypatch):
    """LLM calls cancelOrder; tracker records cancelled order."""

    async def fake_generate(
        messages, *, function_tools, temperature, max_tool_calls, **kwargs
    ):
        handlers = {t.name: t.handler for t in function_tools}
        await handlers["cancelOrder"]()
        return "Your order has been cancelled."

    monkeypatch.setattr(llm_client, "generate_text_with_tools", fake_generate)

    async def fake_cancel(session_id, creds=None):
        return {"success": True, "error": None}

    monkeypatch.setattr(orchestrator_mod, "cancelOrder", fake_cancel)
    _mock_save_clarification(monkeypatch)
    _mock_get_clarification(monkeypatch)

    agent = ExecutionAgent(system_prompt="")
    result = asyncio.run(
        agent.run(
            parsed_requests=_request(
                intent=ParsedRequestIntent.CANCEL_ORDER,
                name="",
                quantity=0,
                request_details="cancel it",
            ),
            context_object=_context_object(latest_customer_message="yes"),
        )
    )

    assert result.agent_reply == "Your order has been cancelled."
    assert result.actions_executed == ["cancelled order"]
    assert result.order_updated is True


def test_execution_agent_update_item_records_tracker(monkeypatch):
    """LLM calls updateItemInOrder; tracker records updated action."""

    async def fake_generate(
        messages, *, function_tools, temperature, max_tool_calls, **kwargs
    ):
        handlers = {t.name: t.handler for t in function_tools}
        await handlers["updateItemInOrder"](
            target={"itemName": "Burger"},
            updates={"addModifiers": ["mod-cheddar"]},
        )
        return "Updated Burger."

    monkeypatch.setattr(llm_client, "generate_text_with_tools", fake_generate)

    async def fake_update(session_id, target, updates, creds=None):
        return {
            "success": True,
            "itemName": "Burger",
            "appliedChanges": "modifier added",
            "error": None,
        }

    monkeypatch.setattr(orchestrator_mod, "updateItemInOrder", fake_update)
    _mock_save_clarification(monkeypatch)
    _mock_get_clarification(monkeypatch)

    agent = ExecutionAgent(system_prompt="")
    result = asyncio.run(
        agent.run(
            parsed_requests=_request(
                intent=ParsedRequestIntent.MODIFY_ITEM,
                name="burger",
                details="cheddar",
                request_details="add cheddar to the burger",
            ),
            context_object=_context_object(),
        )
    )

    assert result.agent_reply == "Updated Burger."
    assert result.actions_executed == ["updated Burger"]
    assert result.order_updated is True


def test_execution_agent_change_quantity_records_tracker(monkeypatch):
    """LLM calls changeItemQuantity; tracker records changed action."""

    async def fake_generate(
        messages, *, function_tools, temperature, max_tool_calls, **kwargs
    ):
        handlers = {t.name: t.handler for t in function_tools}
        await handlers["changeItemQuantity"](
            target={"itemName": "Burger"},
            newQuantity=3,
        )
        return "Changed Burger to 3."

    monkeypatch.setattr(llm_client, "generate_text_with_tools", fake_generate)

    async def fake_change(session_id, target, new_quantity, creds=None):
        return {"success": True, "itemName": "Burger", "newQuantity": 3, "error": None}

    monkeypatch.setattr(orchestrator_mod, "changeItemQuantity", fake_change)
    _mock_save_clarification(monkeypatch)
    _mock_get_clarification(monkeypatch)

    agent = ExecutionAgent(system_prompt="")
    result = asyncio.run(
        agent.run(
            parsed_requests=_request(
                intent=ParsedRequestIntent.CHANGE_ITEM_NUMBER,
                name="burger",
                quantity=3,
                request_details="make it 3 burgers",
            ),
            context_object=_context_object(),
        )
    )

    assert result.agent_reply == "Changed Burger to 3."
    assert result.actions_executed == ["changed Burger to 3"]
    assert result.order_updated is True


def test_execution_agent_no_mutations_when_llm_does_not_call_tools(monkeypatch):
    """If the LLM returns without calling any tools, tracker stays empty."""

    async def fake_generate(
        messages, *, function_tools, temperature, max_tool_calls, **kwargs
    ):
        return "What would you like to order?"

    monkeypatch.setattr(llm_client, "generate_text_with_tools", fake_generate)
    _mock_save_clarification(monkeypatch)
    _mock_get_clarification(monkeypatch)

    agent = ExecutionAgent(system_prompt="")
    result = asyncio.run(
        agent.run(
            parsed_requests=_request(intent=ParsedRequestIntent.GREETING),
            context_object=_context_object(),
        )
    )

    assert result.agent_reply == "What would you like to order?"
    assert result.actions_executed == []
    assert result.order_updated is False


def test_execution_agent_uses_execution_prompt_with_text_tool_calling(monkeypatch):
    observed: dict[str, object] = {}

    async def _fake_generate_text_with_tools(
        messages,
        *,
        function_tools,
        temperature: float,
        max_tool_calls: int,
        max_output_tokens=None,
        model: str | None = None,
    ) -> str:
        del max_output_tokens
        observed["messages"] = messages
        observed["function_tools"] = function_tools
        observed["temperature"] = temperature
        observed["max_tool_calls"] = max_tool_calls
        observed["model"] = model
        return "Customer-facing SMS"

    monkeypatch.setattr(
        llm_client, "generate_text_with_tools", _fake_generate_text_with_tools
    )
    _mock_save_clarification(monkeypatch)
    _mock_get_clarification(monkeypatch)

    agent = ExecutionAgent()
    result = asyncio.run(
        agent.run(
            parsed_requests=_request(
                intent=ParsedRequestIntent.ADD_ITEM,
                confidence=ParsedRequestConfidenceLevel.LOW,
                request_details="maybe the spicy thing",
            ),
            context_object=_context_object(
                latest_customer_message="maybe the spicy thing"
            ),
        )
    )

    assert result.agent_reply == "Customer-facing SMS"
    assert result.pending_clarifications == ["maybe the spicy thing"]
    assert result.order_updated is False
    assert observed["messages"][0]["role"] == "system"
    assert "You are the Order Execution Agent" in observed["messages"][0]["content"]
    assert (
        "Return ONLY a customer-facing SMS reply." in observed["messages"][0]["content"]
    )
    assert '"parsed_requests"' in observed["messages"][1]["content"]
    assert '"tools"' in observed["messages"][1]["content"]
    # All 14 tools are passed to LLM
    assert len(observed["function_tools"]) == 14


def test_execution_agent_system_prompt_contains_validation_tool_rules():
    from src.chatbot.promptsv2 import DEFAULT_EXECUTION_AGENT_SYSTEM_PROMPT

    prompt = DEFAULT_EXECUTION_AGENT_SYSTEM_PROMPT
    assert "validateRequestedItem" in prompt
    assert "validateRequestedItem — details string:" in prompt
    assert "TOOL CALLING RULES" in prompt
    assert "NEVER call mutation tools" in prompt
    assert "Do you want to add anything else?" in prompt


def test_execution_agent_passes_all_tools_to_llm(monkeypatch):
    observed: dict[str, object] = {}

    async def _fake_generate_text_with_tools(
        messages,
        *,
        function_tools,
        temperature: float,
        max_tool_calls: int,
        max_output_tokens=None,
        model: str | None = None,
    ) -> str:
        del messages, temperature, max_tool_calls, max_output_tokens, model
        observed["tool_names"] = [t.name for t in function_tools]
        return "ok"

    monkeypatch.setattr(
        llm_client, "generate_text_with_tools", _fake_generate_text_with_tools
    )
    _mock_save_clarification(monkeypatch)
    _mock_get_clarification(monkeypatch)

    agent = ExecutionAgent()
    asyncio.run(
        agent.run(
            parsed_requests=_request(
                intent=ParsedRequestIntent.ADD_ITEM,
                confidence=ParsedRequestConfidenceLevel.LOW,
                request_details="a burger",
            ),
            context_object=_context_object(latest_customer_message="a burger"),
        )
    )

    expected_tools = [
        "validateRequestedItem",
        "addItemsToOrder",
        "replaceItemInOrder",
        "removeItemFromOrder",
        "changeItemQuantity",
        "updateItemInOrder",
        "calcOrderPrice",
        "confirmOrder",
        "cancelOrder",
        "getMenuLink",
        "getItemsNotAvailableToday",
        "humanInterventionNeeded",
        "getPreviousOrdersDetails",
        "requestPickupTime",
    ]
    assert observed["tool_names"] == expected_tools


def test_execution_agent_build_tools_passes_runtime_creds_to_validate_requested_item(
    monkeypatch,
):
    observed: dict[str, object] = {}

    async def _fake_validate_requested_item(
        *,
        itemName: str,
        details: str | None = None,
        merchant_id: str | None = None,
        creds: dict | None = None,
    ) -> dict:
        observed["itemName"] = itemName
        observed["details"] = details
        observed["merchant_id"] = merchant_id
        observed["creds"] = creds
        return {
            "exactMatch": {"id": "item-1", "name": "Burger"},
            "candidates": [{"id": "item-1", "name": "Burger"}],
            "matchConfidence": "exact",
            "itemId": "item-1",
            "available": True,
            "allValid": True,
            "error": None,
        }

    monkeypatch.setattr(
        orchestrator_mod, "validateRequestedItem", _fake_validate_requested_item
    )

    tools = ExecutionAgent(system_prompt="").build_tools(
        orchestrator_mod.ExecutionToolRuntime(
            context=orchestrator_mod.ExecutionAgentContext(
                session_id="session-1",
                merchant_id="merchant-from-creds",
                clover_creds={"merchant_id": "merchant-from-creds", "token": "secret"},
                clover_error=None,
            )
        )
    )

    payload = asyncio.run(
        tools[0].handler(
            itemName="burger",
            details="spicy",
        )
    )

    assert observed["itemName"] == "burger"
    assert observed["details"] == "spicy"
    assert observed["merchant_id"] == "merchant-from-creds"
    assert observed["creds"] == {
        "merchant_id": "merchant-from-creds",
        "token": "secret",
    }
    assert payload["matchConfidence"] == "exact"


def test_execution_agent_build_tools_registers_extended_toolset():
    tools = ExecutionAgent(system_prompt="").build_tools(
        orchestrator_mod.ExecutionToolRuntime(
            context=orchestrator_mod.ExecutionAgentContext(
                session_id="session-1",
                merchant_id="merchant-1",
                clover_creds={"merchant_id": "merchant-1", "token": "secret"},
                clover_error=None,
            )
        )
    )

    assert [tool.name for tool in tools] == [
        "validateRequestedItem",
        "addItemsToOrder",
        "replaceItemInOrder",
        "removeItemFromOrder",
        "changeItemQuantity",
        "updateItemInOrder",
        "calcOrderPrice",
        "confirmOrder",
        "cancelOrder",
        "getMenuLink",
        "getItemsNotAvailableToday",
        "humanInterventionNeeded",
        "getPreviousOrdersDetails",
        "requestPickupTime",
    ]


class _FakeGemini503(Exception):
    """Mimics ``google.genai.errors.ServerError`` exposing HTTP status as ``code``."""

    code = 503


def test_execution_agent_retries_gemini_503_then_succeeds(monkeypatch):
    monkeypatch.setattr(orchestrator_mod, "_GEMINI_503_BACKOFF_SEC", 0.0)
    calls = 0

    async def fake_generate(
        messages, *, function_tools, temperature, max_tool_calls, **kwargs
    ):
        nonlocal calls
        del messages, function_tools, temperature, max_tool_calls, kwargs
        calls += 1
        if calls < 3:
            raise AIServiceError(
                "Gemini request failed: overloaded"
            ) from _FakeGemini503()
        return "Recovered after outages."

    monkeypatch.setattr(llm_client, "generate_text_with_tools", fake_generate)
    _mock_save_clarification(monkeypatch)
    _mock_get_clarification(monkeypatch)

    agent = ExecutionAgent(system_prompt="")
    result = asyncio.run(
        agent.run(
            parsed_requests=_request(intent=ParsedRequestIntent.GREETING),
            context_object=_context_object(),
        )
    )

    assert calls == 3
    assert result.agent_reply == "Recovered after outages."


def test_execution_agent_stops_after_ten_gemini_503_attempts(monkeypatch):
    monkeypatch.setattr(orchestrator_mod, "_GEMINI_503_BACKOFF_SEC", 0.0)
    calls = 0

    async def fake_generate(
        messages, *, function_tools, temperature, max_tool_calls, **kwargs
    ):
        nonlocal calls
        del messages, function_tools, temperature, max_tool_calls, kwargs
        calls += 1
        raise AIServiceError("Gemini request failed: unavailable") from _FakeGemini503()

    monkeypatch.setattr(llm_client, "generate_text_with_tools", fake_generate)
    _mock_save_clarification(monkeypatch)
    _mock_get_clarification(monkeypatch)

    agent = ExecutionAgent(system_prompt="")

    with pytest.raises(AIServiceError, match="Gemini request failed"):
        asyncio.run(
            agent.run(
                parsed_requests=_request(intent=ParsedRequestIntent.GREETING),
                context_object=_context_object(),
            )
        )

    assert calls == 10


def test_execution_agent_does_not_retry_non_503_gemini_errors(monkeypatch):
    monkeypatch.setattr(orchestrator_mod, "_GEMINI_503_BACKOFF_SEC", 0.0)
    calls = 0

    async def fake_generate(
        messages, *, function_tools, temperature, max_tool_calls, **kwargs
    ):
        nonlocal calls
        del messages, function_tools, temperature, max_tool_calls, kwargs
        calls += 1
        raise AIServiceError("Gemini request failed: rate limited") from RuntimeError()

    monkeypatch.setattr(llm_client, "generate_text_with_tools", fake_generate)
    _mock_save_clarification(monkeypatch)
    _mock_get_clarification(monkeypatch)

    agent = ExecutionAgent(system_prompt="")

    with pytest.raises(AIServiceError, match="Gemini request failed"):
        asyncio.run(
            agent.run(
                parsed_requests=_request(intent=ParsedRequestIntent.GREETING),
                context_object=_context_object(),
            )
        )

    assert calls == 1


def test_execution_tracker_mutated_by_tool_wrappers(monkeypatch):
    """ExecutionTracker is mutated when tool wrappers fire on success."""

    async def fake_add(session_id, items, creds=None):
        return {
            "success": True,
            "addedItems": [{"name": "Burger", "quantity": 2}],
            "failedItems": [],
            "updatedOrderTotal": 2198,
        }

    monkeypatch.setattr(orchestrator_mod, "addItemsToOrder", fake_add)

    tracker = orchestrator_mod.ExecutionTracker()
    runtime = orchestrator_mod.ExecutionToolRuntime(
        context=orchestrator_mod.ExecutionAgentContext(
            session_id="session-1",
            merchant_id="merchant-1",
            clover_creds=None,
            clover_error=None,
        )
    )
    tools = ExecutionAgent(system_prompt="")._build_tools(runtime, tracker=tracker)
    add_tool = next(t for t in tools if t.name == "addItemsToOrder")

    asyncio.run(add_tool.handler(items=[{"itemId": "item-1", "quantity": 2}]))

    assert tracker.actions_executed == ["added 2x Burger"]
    assert tracker.order_updated is True


# ─── run_single self-escalation ──────────────────────────────────────────────


def test_run_single_escalates_when_qa_count_exceeds_max(monkeypatch):
    """Calls humanInterventionNeeded_idempotent and sets escalated=True when qa_count_after > MAX."""
    from src.config import settings

    async def fake_generate(messages, *, function_tools, temperature, max_tool_calls, **kwargs):
        return "Which size would you like?"

    monkeypatch.setattr(llm_client, "generate_text_with_tools", fake_generate)

    escalated_calls: list[dict] = []

    async def fake_escalate(*, session_id, escalation_type, merchant_id):
        escalated_calls.append({"session_id": session_id, "escalation_type": escalation_type})
        return {"success": True}

    monkeypatch.setattr(orchestrator_mod, "humanInterventionNeeded_idempotent", fake_escalate)

    entry = {
        "entry_id": "e-1",
        "status": "pending",
        "parsed_item": {"Intent": "add_item", "Request_items": {"name": "burger"}},
        "qa": [{"question": f"q{i}", "answer": f"a{i}"} for i in range(settings.MAX_CLARIFICATION_QUESTIONS)],
    }
    agent = ExecutionAgent(system_prompt="")
    result = asyncio.run(agent.run_single(entry=entry, context_object=_context_object()))

    assert result.escalated is True
    assert len(escalated_calls) == 1
    assert escalated_calls[0]["escalation_type"] == "questions_about_their_order"


def test_run_single_does_not_escalate_on_success_path(monkeypatch):
    """Does not call humanInterventionNeeded_idempotent when LLM returns no questions."""
    from src.config import settings

    async def fake_generate(messages, *, function_tools, temperature, max_tool_calls, **kwargs):
        return "I added the burger."

    monkeypatch.setattr(llm_client, "generate_text_with_tools", fake_generate)

    escalated_calls: list[dict] = []

    async def fake_escalate(*, session_id, escalation_type, merchant_id):
        escalated_calls.append({})
        return {"success": True}

    monkeypatch.setattr(orchestrator_mod, "humanInterventionNeeded_idempotent", fake_escalate)

    entry = {
        "entry_id": "e-2",
        "status": "pending",
        "parsed_item": {"Intent": "add_item", "Request_items": {"name": "burger"}},
        # qa already over max — but success path must not escalate regardless
        "qa": [{"question": f"q{i}", "answer": f"a{i}"} for i in range(settings.MAX_CLARIFICATION_QUESTIONS + 1)],
    }
    agent = ExecutionAgent(system_prompt="")
    result = asyncio.run(agent.run_single(entry=entry, context_object=_context_object()))

    assert result.escalated is False
    assert len(escalated_calls) == 0


def test_run_single_does_not_escalate_when_threshold_not_breached(monkeypatch):
    """Does not escalate when qa_count_after <= MAX_CLARIFICATION_QUESTIONS."""
    async def fake_generate(messages, *, function_tools, temperature, max_tool_calls, **kwargs):
        return "Which size would you like?"

    monkeypatch.setattr(llm_client, "generate_text_with_tools", fake_generate)

    escalated_calls: list[dict] = []

    async def fake_escalate(*, session_id, escalation_type, merchant_id):
        escalated_calls.append({})
        return {"success": True}

    monkeypatch.setattr(orchestrator_mod, "humanInterventionNeeded_idempotent", fake_escalate)

    entry = {
        "entry_id": "e-3",
        "status": "pending",
        "parsed_item": {"Intent": "add_item", "Request_items": {"name": "burger"}},
        "qa": [],  # qa_count_after = 0 + 1 = 1, well under MAX
    }
    agent = ExecutionAgent(system_prompt="")
    result = asyncio.run(agent.run_single(entry=entry, context_object=_context_object()))

    assert result.escalated is False
    assert len(escalated_calls) == 0
