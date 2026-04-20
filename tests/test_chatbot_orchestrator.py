import asyncio

from src.chatbot import orchestrator as orchestrator_mod
from src.chatbot.orchestrator import Orchestrator
from src.chatbot.schema import (
    ChatbotV2MessageRequest,
    CurrentOrderDetails,
    ExecutionAgentContext,
    ExecutionAgentResult,
    ParsedRequestConfidenceLevel,
    ParsedRequestIntent,
    ParsedRequestItem,
    ParsedRequestItems,
    ParsedRequestsPayload,
    ParsingAgentContext,
    ParsingAgentResult,
)


class _FakeParsingAgent:
    def __init__(self, events: list[tuple]):
        self.events = events

    async def run(
        self, *, context: ParsingAgentContext, prompts=None
    ) -> ParsingAgentResult:
        del prompts
        self.events.append(
            (
                "parsing",
                context.current_order_details.order_id,
                tuple(context.latest_k_messages_by_customer),
                context.most_recent_message,
                context.session_id,
                context.merchant_id,
            )
        )
        return ParsingAgentResult(
            context=context,
            parsed_requests=ParsedRequestsPayload(
                data=[
                    ParsedRequestItem(
                        intent=ParsedRequestIntent.ADD_ITEM,
                        confidence_level=ParsedRequestConfidenceLevel.HIGH,
                        request_items=ParsedRequestItems(
                            name="burger",
                            quantity=1,
                            details="",
                        ),
                        request_details="test parse",
                    )
                ]
            ),
        )


class _FakeExecutionAgent:
    def __init__(self, events: list[tuple]):
        self.events = events

    async def run(
        self,
        *,
        parsed_requests: ParsedRequestsPayload,
        context_object,
    ) -> ExecutionAgentResult:
        self.events.append(
            (
                "execution",
                len(parsed_requests.data),
                parsed_requests.data[0].intent,
                context_object.latest_customer_message,
                context_object.session_id,
                context_object.merchant_id,
                context_object.current_order_details.order_id,
                tuple(context_object.latest_k_messages_by_customer),
            )
        )
        return ExecutionAgentResult(
            agent_reply="stubbed system response",
            session_id=context_object.session_id,
            actions_executed=["added 1 x burger"],
            pending_clarifications=[],
            order_updated=True,
        )


def test_orchestrator_builds_server_side_context_and_calls_agents_in_order(monkeypatch):
    events: list[tuple] = []
    orchestrator = Orchestrator(
        parsing_agent=_FakeParsingAgent(events),
        execution_agent=_FakeExecutionAgent(events),
    )

    async def _fake_get_previous_messages(session_id: str, k: int | None = None):
        assert session_id == "session-123"
        assert k == orchestrator_mod.settings.DEFAULT_PREVIOUS_MESSAGES_K
        return {
            "success": True,
            "messages": [
                {
                    "role": "customer",
                    "content": "first message",
                    "timestamp": "2026-04-18T11:00:00Z",
                },
                {
                    "role": "agent",
                    "content": "agent reply",
                    "timestamp": "2026-04-18T11:00:01Z",
                },
                {
                    "role": "customer",
                    "content": "second message",
                    "timestamp": "2026-04-18T11:00:02Z",
                },
            ],
            "error": None,
        }

    async def _fake_get_order_line_items(session_id: str, creds=None):
        assert session_id == "session-123"
        return {
            "success": True,
            "orderId": "order-7",
            "lineItems": [
                {
                    "lineItemId": "li-1",
                    "name": "Burger",
                    "quantity": 2,
                    "price": 1099,
                }
            ],
            "orderTotal": 1099,
            "error": None,
        }

    monkeypatch.setattr(
        orchestrator_mod, "getPreviousKMessages", _fake_get_previous_messages
    )
    monkeypatch.setattr(
        orchestrator_mod, "getOrderLineItems", _fake_get_order_line_items
    )
    monkeypatch.setattr(
        orchestrator_mod,
        "prepare_clover_data",
        lambda db, settings, merchant_id: asyncio.sleep(
            0, result={"merchant_id": merchant_id, "token": "secret"}
        ),
    )
    monkeypatch.setattr(
        orchestrator_mod,
        "cache_list_append",
        lambda key, value: asyncio.sleep(0),
    )
    monkeypatch.setattr(
        orchestrator_mod,
        "saveClarificationAndIntent",
        lambda session_id, clarification_questions, parsed_intents, **kwargs: asyncio.sleep(0),
    )

    response = asyncio.run(
        orchestrator.handle_message(
            ChatbotV2MessageRequest(
                user_message="Need a burger",
                session_id="session-123",
                merchant_id="merchant-456",
            )
        )
    )

    assert events == [
        (
            "parsing",
            "order-7",
            ("first message", "second message"),
            "Need a burger",
            "session-123",
            "merchant-456",
        ),
        (
            "execution",
            1,
            ParsedRequestIntent.ADD_ITEM,
            "Need a burger",
            "session-123",
            "merchant-456",
            "order-7",
            ("first message", "second message"),
        ),
    ]
    assert response.system_response == "stubbed system response"
    assert response.session_id == "session-123"


def test_orchestrator_uses_empty_defaults_when_context_fetch_fails(monkeypatch):
    orchestrator = Orchestrator()

    async def _fake_get_previous_messages(session_id: str, k: int | None = None):
        del session_id
        del k
        return {
            "success": False,
            "messages": [],
            "error": "redis down",
        }

    async def _fake_get_order_line_items(session_id: str, creds=None):
        del session_id
        return {
            "success": False,
            "orderId": "",
            "lineItems": [],
            "orderTotal": 0,
            "error": "clover down",
        }

    monkeypatch.setattr(
        orchestrator_mod, "getPreviousKMessages", _fake_get_previous_messages
    )
    monkeypatch.setattr(
        orchestrator_mod, "getOrderLineItems", _fake_get_order_line_items
    )

    context = asyncio.run(
        orchestrator._build_parsing_context(
            ChatbotV2MessageRequest(
                user_message="Need a burger",
                session_id="session-123",
                merchant_id="merchant-456",
            )
        )
    )

    prepared = orchestrator.prepare_agent_context(
        parsed_input=ParsingAgentResult(
            context=context,
            parsed_requests=ParsedRequestsPayload(data=[]),
        ),
        execution_context=ExecutionAgentContext(
            session_id="session-123",
            merchant_id="merchant-456",
            clover_creds=None,
            clover_error="creds down",
        ),
    )

    assert context.current_order_details == CurrentOrderDetails(
        order_id="",
        line_items=[],
        order_total=0,
        raw_error="clover down",
    )
    assert context.latest_k_messages_by_customer == []
    assert prepared.clover_error == "creds down"
    assert prepared.latest_customer_message == "Need a burger"
