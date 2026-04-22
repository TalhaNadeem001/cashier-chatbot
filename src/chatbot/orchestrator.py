from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, TypeVar

from src import firebase as _firebase
from src.chatbot import llm_client as gemini_client
from src.chatbot.constants import _PARSE_VALIDATION_ERROR_PREFIX
from src.chatbot.exceptions import AIServiceError
from src.chatbot.llm_messages import LLMMessage
from src.chatbot.promptsv2 import (
    DEFAULT_EXECUTION_AGENT_SYSTEM_PROMPT,
    DEFAULT_PARSING_AGENT_PROMPTS,
)
from src.chatbot.schema import (
    ChatbotV2MessageRequest,
    ChatbotV2MessageResponse,
    CurrentOrderDetails,
    CurrentOrderLineItem,
    ExecutionAgentContext,
    ExecutionAgentPromptContext,
    ExecutionAgentResult,
    ExecutionAgentToolDescriptor,
    ParsedRequestConfidenceLevel,
    ParsedRequestsPayload,
    PreparedExecutionContext,
    ParsingAgentContext,
    ParsingAgentPromptContext,
    ParsingAgentResult,
    ParsingAgentPrompts,
)
from src.chatbot.tools import (
    addItemsToOrder,
    calcOrderPrice,
    cancelOrder,
    changeItemQuantity,
    confirmOrder,
    getMenuLink,
    getItemsNotAvailableToday,
    getOrderLineItems,
    getPreviousKMessages,
    getPreviousOrdersDetails,
    humanInterventionNeeded,
    prepare_clover_data,
    replaceItemInOrder,
    removeItemFromOrder,
    suggestedPickupTime,
    updateItemInOrder,
    validateRequestedItem,
)
from datetime import datetime, timezone

from src.cache import cache_get, cache_list_append
from src.chatbot.utils import (
    _session_messages_redis_key,
    _session_status_redis_key,
    extract_questions_from_reply,
    getClarificationAndIntent,
    saveClarificationAndIntent,
)
from src.config import settings

_GEMINI_503_MAX_ATTEMPTS = 10
_GEMINI_503_BACKOFF_SEC = 2.0
_GEMINI_429_MAX_ATTEMPTS = 6
_GEMINI_429_BACKOFF_SEC = 5.0

_T = TypeVar("_T")


def _is_gemini_http_503(exc: AIServiceError) -> bool:
    """True when ``exc`` wraps a Gemini/API HTTP 503 (service unavailable)."""
    err: BaseException | None = exc
    seen: set[int] = set()
    while err is not None and id(err) not in seen:
        seen.add(id(err))
        status = getattr(err, "code", None)
        if status is None:
            status = getattr(err, "status_code", None)
        if status == 503:
            return True
        response = getattr(err, "response", None)
        if response is not None:
            resp_status = getattr(response, "status_code", None)
            if resp_status == 503:
                return True
        err = err.__cause__
    return False


def _is_gemini_http_429(exc: AIServiceError) -> bool:
    """True when ``exc`` wraps a Gemini/API HTTP 429 (rate limit / resource exhausted)."""
    err: BaseException | None = exc
    seen: set[int] = set()
    while err is not None and id(err) not in seen:
        seen.add(id(err))
        status = getattr(err, "code", None)
        if status is None:
            status = getattr(err, "status_code", None)
        if status == 429:
            return True
        response = getattr(err, "response", None)
        if response is not None:
            resp_status = getattr(response, "status_code", None)
            if resp_status == 429:
                return True
        err = err.__cause__
    return False


async def _gemini_service_call_with_retries(
    *,
    log_label: str,
    extra_fields: str,
    call: Callable[[], Awaitable[_T]],
) -> _T:
    attempt = 0
    while True:
        attempt += 1
        try:
            return await call()
        except AIServiceError as exc:
            is_503 = _is_gemini_http_503(exc)
            is_429 = _is_gemini_http_429(exc)
            print(
                f"{log_label} Gemini call failed",
                f"trial={attempt}",
                f"http_503={is_503}",
                f"http_429={is_429}",
                extra_fields,
                f"error={exc!r}",
            )
            if is_503 and attempt < _GEMINI_503_MAX_ATTEMPTS:
                print(
                    f"{log_label} backing off before retry (503)",
                    f"sleep_s={_GEMINI_503_BACKOFF_SEC}",
                    f"next_trial={attempt + 1}",
                )
                await asyncio.sleep(_GEMINI_503_BACKOFF_SEC)
                continue
            elif is_429 and attempt < _GEMINI_429_MAX_ATTEMPTS:
                print(
                    f"{log_label} backing off before retry (429)",
                    f"sleep_s={_GEMINI_429_BACKOFF_SEC}",
                    f"next_trial={attempt + 1}",
                )
                await asyncio.sleep(_GEMINI_429_BACKOFF_SEC)
                continue
            raise


_EXECUTION_AGENT_SYSTEM_PROMPT = DEFAULT_EXECUTION_AGENT_SYSTEM_PROMPT

_VALIDATE_REQUESTED_ITEM_PARAMETERS_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "itemName": {
            "type": "string",
            "description": "The item name exactly as the customer said it. Do not normalize spelling.",
        },
        "details": {
            "type": ["string", "null"],
            "description": "Raw modifier or qualifier string from the customer (e.g. 'lemon pepper, extra crispy'). Pass None when absent. Do not pre-split.",
        },
    },
    "required": ["itemName"],
    "additionalProperties": False,
}
_ADD_ITEMS_TO_ORDER_PARAMETERS_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "itemId": {"type": "string"},
                    "quantity": {"type": "integer", "minimum": 1},
                    "modifiers": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "note": {"type": ["string", "null"]},
                },
                "required": ["itemId"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}
_REPLACE_ITEM_IN_ORDER_PARAMETERS_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "replacement": {
            "type": "object",
            "properties": {
                "itemId": {"type": "string"},
                "quantity": {"type": "integer", "minimum": 1},
                "modifiers": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "note": {"type": ["string", "null"]},
            },
            "required": ["itemId"],
            "additionalProperties": False,
        },
        "lineItemId": {"type": "string"},
        "orderPosition": {"type": "integer", "minimum": 1},
        "itemName": {"type": "string"},
    },
    "required": ["replacement"],
    "additionalProperties": False,
}
_REMOVE_ITEM_FROM_ORDER_PARAMETERS_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "target": {
            "type": "object",
            "properties": {
                "orderPosition": {"type": "integer", "minimum": 1},
                "itemName": {"type": "string"},
            },
            "additionalProperties": False,
        }
    },
    "required": ["target"],
    "additionalProperties": False,
}
_CHANGE_ITEM_QUANTITY_PARAMETERS_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "target": {
            "type": "object",
            "properties": {
                "lineItemId": {"type": "string"},
                "orderPosition": {"type": "integer", "minimum": 1},
                "itemName": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "newQuantity": {"type": "integer", "minimum": 1},
    },
    "required": ["target", "newQuantity"],
    "additionalProperties": False,
}
_UPDATE_ITEM_IN_ORDER_PARAMETERS_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "target": {
            "type": "object",
            "properties": {
                "lineItemId": {"type": "string"},
                "orderPosition": {"type": "integer", "minimum": 1},
                "itemName": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "updates": {
            "type": "object",
            "properties": {
                "addModifiers": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "removeModifiers": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "note": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        },
    },
    "required": ["target", "updates"],
    "additionalProperties": False,
}
_NO_ARGUMENTS_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}
_GET_MENU_LINK_PARAMETERS_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}
_GET_ITEMS_NOT_AVAILABLE_TODAY_PARAMETERS_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}
_HUMAN_INTERVENTION_NEEDED_PARAMETERS_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reason": {
            "type": "string",
            "description": "Short plain-text description of why human intervention is needed.",
        }
    },
    "required": ["reason"],
    "additionalProperties": False,
}
_GET_PREVIOUS_ORDERS_DETAILS_PARAMETERS_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "limit": {
            "type": "integer",
            "description": "Maximum number of past orders to return. Defaults to 3.",
        }
    },
    "additionalProperties": False,
}
_SUGGESTED_PICKUP_TIME_PARAMETERS_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "pickup_time_minutes": {
            "type": "integer",
            "description": "Customer's suggested pickup time converted to whole minutes from now.",
        }
    },
    "required": ["pickup_time_minutes"],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class ExecutionToolRuntime:
    context: ExecutionAgentContext


@dataclass(slots=True)
class ExecutionTracker:
    actions_executed: list[str] = field(default_factory=list)
    order_updated: bool = False


class Orchestrator:
    def __init__(
        self,
        *,
        parsing_agent: ParsingAgent | None = None,
        execution_agent: ExecutionAgent | None = None,
    ) -> None:
        self.parsing_agent = parsing_agent or ParsingAgent()
        self.execution_agent = execution_agent or ExecutionAgent()

    async def handle_message(
        self,
        request: ChatbotV2MessageRequest,
    ) -> ChatbotV2MessageResponse:
        now = datetime.now(timezone.utc).isoformat()
        redis_key = _session_messages_redis_key(request.session_id)
        await cache_list_append(
            redis_key,
            json.dumps(
                {"role": "user", "content": request.user_message, "timestamp": now}
            ),
        )

        execution_context = await self._build_execution_context(request)
        context = await self._build_parsing_context(request, clover_creds=execution_context.clover_creds)
        parsed_input = await self.parsing_agent.run(context=context)
        session_status = await cache_get(_session_status_redis_key(request.session_id))
        is_order_confirmed = session_status == "confirmed"
        prepared_context = self.prepare_agent_context(
            parsed_input=parsed_input,
            execution_context=execution_context,
            is_order_confirmed=is_order_confirmed,
        )
        execution_result = await self.execution_agent.run(
            parsed_requests=parsed_input.parsed_requests,
            context_object=prepared_context,
        )

        ai_now = datetime.now(timezone.utc).isoformat()
        await cache_list_append(
            redis_key,
            json.dumps(
                {
                    "role": "assistant",
                    "content": execution_result.agent_reply,
                    "timestamp": ai_now,
                }
            ),
        )

        return ChatbotV2MessageResponse(
            system_response=execution_result.agent_reply,
            session_id=execution_result.session_id,
        )

    async def _build_parsing_context(
        self,
        request: ChatbotV2MessageRequest,
        clover_creds: dict | None = None,
    ) -> ParsingAgentContext:
        current_order_details = await self._load_current_order_details(
            request.session_id, creds=clover_creds
        )
        latest_k_messages_by_customer = await self._load_latest_k_customer_messages(
            request.session_id
        )
        raw = await getClarificationAndIntent(request.session_id)
        previous_agent_questions = raw.get("agent_questions", []) if raw["success"] else []

        return ParsingAgentContext(
            session_id=request.session_id,
            merchant_id=request.merchant_id,
            current_order_details=current_order_details,
            most_recent_message=request.user_message,
            latest_k_messages_by_customer=latest_k_messages_by_customer,
            previous_agent_questions=previous_agent_questions,
        )

    async def _load_current_order_details(self, session_id: str, creds: dict | None = None) -> CurrentOrderDetails:
        order_result = await getOrderLineItems(session_id, creds=creds)
        if not order_result.get("success"):
            return CurrentOrderDetails(
                order_id="",
                line_items=[],
                order_total=0,
                raw_error=order_result.get("error"),
            )

        line_items = [
            CurrentOrderLineItem(
                line_item_id=str(item.get("lineItemId", "")),
                name=str(item.get("name", "")),
                quantity=int(item.get("quantity", 0) or 0),
                price=int(item.get("price", 0) or 0),
            )
            for item in order_result.get("lineItems", [])
        ]
        return CurrentOrderDetails(
            order_id=str(order_result.get("orderId", "")),
            line_items=line_items,
            order_total=int(order_result.get("orderTotal", 0) or 0),
            raw_error=None,
        )

    async def _load_latest_k_customer_messages(self, session_id: str) -> list[str]:
        history_result = await getPreviousKMessages(
            session_id,
            settings.DEFAULT_PREVIOUS_MESSAGES_K,
        )
        if not history_result.get("success"):
            return []

        return [
            str(message.get("content", ""))
            for message in history_result.get("messages", [])
            if message.get("role") == "customer"
            and str(message.get("content", "")).strip()
        ]

    async def _build_execution_context(
        self,
        request: ChatbotV2MessageRequest,
    ) -> ExecutionAgentContext:
        try:
            clover_creds = await prepare_clover_data(
                _firebase.firebaseDatabase, settings, request.merchant_id
            )
        except Exception as exc:
            return ExecutionAgentContext(
                session_id=request.session_id,
                merchant_id=request.merchant_id,
                clover_creds=None,
                clover_error=str(exc),
            )

        resolved_merchant_id = clover_creds.get("merchant_id") or request.merchant_id
        return ExecutionAgentContext(
            session_id=request.session_id,
            merchant_id=resolved_merchant_id,
            clover_creds=clover_creds,
            clover_error=None,
        )

    def prepareAgentContext(
        self,
        *,
        parsed_input: ParsingAgentResult,
        execution_context: ExecutionAgentContext,
        is_order_confirmed: bool = False,
    ) -> PreparedExecutionContext:
        return self.prepare_agent_context(
            parsed_input=parsed_input,
            execution_context=execution_context,
            is_order_confirmed=is_order_confirmed,
        )

    def prepare_agent_context(
        self,
        *,
        parsed_input: ParsingAgentResult,
        execution_context: ExecutionAgentContext,
        is_order_confirmed: bool = False,
    ) -> PreparedExecutionContext:
        return PreparedExecutionContext(
            session_id=execution_context.session_id,
            merchant_id=execution_context.merchant_id,
            latest_customer_message=parsed_input.context.most_recent_message,
            current_order_details=parsed_input.context.current_order_details,
            latest_k_messages_by_customer=parsed_input.context.latest_k_messages_by_customer,
            clover_creds=execution_context.clover_creds,
            clover_error=execution_context.clover_error,
            is_order_confirmed=is_order_confirmed,
        )


class ParsingAgent:
    def __init__(
        self,
        *,
        model: str | None = None,
        prompts: ParsingAgentPrompts | None = None,
    ) -> None:
        self.model = model or settings.PARSING_AGENT_GEMINI_MODEL
        self.prompts = prompts or DEFAULT_PARSING_AGENT_PROMPTS
        print(
            "[ParsingAgent] init",
            f"model={self.model}",
            f"prompts={'custom' if prompts is not None else 'default'}",
        )

    async def run(
        self,
        *,
        context: ParsingAgentContext,
        prompts: ParsingAgentPrompts | None = None,
    ) -> ParsingAgentResult:
        active_prompts = prompts or self.prompts
        msg_preview = context.most_recent_message.replace("\n", " ")[:120]
        print(
            "[ParsingAgent] run start",
            f"session_id={context.session_id}",
            f"merchant_id={context.merchant_id}",
            f"most_recent_message_preview={msg_preview!r}",
            f"run_prompts_override={'yes' if prompts is not None else 'no'}",
        )

        try:
            parsed_requests = await self._generate_parse_with_gemini_503_retries(
                context=context,
                prompts=active_prompts,
                strict_retry=False,
            )
            print(f"[ParsingAgent] parsed_requests={parsed_requests!r}")
        except AIServiceError as exc:
            will_retry = self._should_retry_on_parse_error(exc)
            print(
                "[ParsingAgent] first parse failed",
                f"error={exc!r}",
                f"will_retry_strict={will_retry}",
            )
            if not will_retry:
                raise
            try:
                parsed_requests = await self._generate_parse_with_gemini_503_retries(
                    context=context,
                    prompts=active_prompts,
                    strict_retry=True,
                )
            except AIServiceError as retry_exc:
                print(
                    "[ParsingAgent] strict retry failed",
                    f"error={retry_exc!r}",
                )
                raise AIServiceError(
                    f"Parsing agent failed after retry: {retry_exc}"
                ) from retry_exc
            print("[ParsingAgent] strict retry succeeded")

        n_items = len(parsed_requests.data)
        intents = [item.intent.value for item in parsed_requests.data]
        print(
            "[ParsingAgent] run done",
            f"parsed_item_count={n_items}",
            f"intents={intents}",
        )

        return ParsingAgentResult(
            context=context,
            parsed_requests=parsed_requests,
        )

    async def _generate_parse_with_gemini_503_retries(
        self,
        *,
        context: ParsingAgentContext,
        prompts: ParsingAgentPrompts,
        strict_retry: bool,
    ) -> ParsedRequestsPayload:
        async def _call() -> ParsedRequestsPayload:
            return await self._generate_parse(
                context=context,
                prompts=prompts,
                strict_retry=strict_retry,
            )

        return await _gemini_service_call_with_retries(
            log_label="[ParsingAgent]",
            extra_fields=f"strict_retry={strict_retry}",
            call=_call,
        )

    async def _generate_parse(
        self,
        *,
        context: ParsingAgentContext,
        prompts: ParsingAgentPrompts,
        strict_retry: bool,
    ) -> ParsedRequestsPayload:
        print(
            "[ParsingAgent] _generate_parse",
            f"strict_retry={strict_retry}",
            f"model={self.model}",
        )
        messages = self._build_messages(
            context=context,
            prompts=prompts,
            strict_retry=strict_retry,
        )
        result = await gemini_client.generate_model(
            messages,
            ParsedRequestsPayload,
            temperature=0,
            model=self.model,
        )
        print(
            "[ParsingAgent] _generate_parse ok",
            f"strict_retry={strict_retry}",
            f"item_count={len(result.data)}",
        )
        return result

    def _build_messages(
        self,
        *,
        context: ParsingAgentContext,
        prompts: ParsingAgentPrompts,
        strict_retry: bool,
    ) -> list[LLMMessage]:
        system_sections = [
            prompts.identity_prompt,
            prompts.input_you_receive_prompt,
            prompts.output_format_prompt,
            prompts.intent_labels_prompt,
            prompts.parsing_rules_prompt,
            prompts.few_shot_examples_prompt,
            prompts.final_reminders_prompt,
            prompts.internal_validation_prompt,
        ]
        if strict_retry:
            system_sections.append(prompts.strict_retry_prompt)
        system_prompt = "\n\n".join(
            section for section in system_sections if section.strip()
        )
        user_content = self._render_context(context)
        print(
            "[ParsingAgent] _build_messages",
            f"strict_retry={strict_retry}",
            f"system_chars={len(system_prompt)}",
            f"user_chars={len(user_content)}",
        )
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

    def _render_context(self, context: ParsingAgentContext) -> str:
        prompt_context = ParsingAgentPromptContext(
            current_order_details=context.current_order_details.model_dump(
                mode="json",
                exclude={"raw_error"},
            ),
            most_recent_message_by_customer=context.most_recent_message,
            latest_k_messages_by_customer=context.latest_k_messages_by_customer,
            previous_agent_questions=context.previous_agent_questions,
        )
        rendered = json.dumps(prompt_context.model_dump(mode="json"), indent=2)
        print(
            "[ParsingAgent] _render_context",
            f"k_tail_messages={len(context.latest_k_messages_by_customer)}",
            f"json_chars={len(rendered)}",
        )
        return rendered

    def _should_retry_on_parse_error(self, error: AIServiceError) -> bool:
        retry = str(error).startswith(_PARSE_VALIDATION_ERROR_PREFIX)
        print(
            "[ParsingAgent] _should_retry_on_parse_error",
            f"retry={retry}",
            f"error_prefix={str(error)[:80]!r}",
        )
        return retry


class ExecutionAgent:
    def __init__(
        self,
        *,
        model: str | None = None,
        max_tool_calls: int | None = None,
        system_prompt: str | None = None,
    ) -> None:
        self.model = model or settings.EXECUTION_AGENT_GEMINI_MODEL
        self.max_tool_calls = (
            max_tool_calls
            if max_tool_calls is not None
            else settings.EXECUTION_AGENT_MAX_TOOL_CALLS
        )
        self.system_prompt = (
            _EXECUTION_AGENT_SYSTEM_PROMPT if system_prompt is None else system_prompt
        )
        print(
            "[ExecutionAgent] init",
            f"model={self.model}",
            f"max_tool_calls={self.max_tool_calls}",
            f"system_prompt={'custom' if system_prompt is not None else 'default'}",
            f"system_prompt_chars={len(self.system_prompt)}",
        )

    async def run(
        self,
        *,
        parsed_requests: ParsedRequestsPayload,
        context_object: PreparedExecutionContext,
    ) -> ExecutionAgentResult:
        tracker = ExecutionTracker()
        runtime = ExecutionToolRuntime(
            context=ExecutionAgentContext(
                session_id=context_object.session_id,
                merchant_id=context_object.merchant_id,
                clover_creds=context_object.clover_creds,
                clover_error=context_object.clover_error,
            )
        )
        active_tools = self._build_tools(runtime, tracker=tracker)

        pending_clarifications = [
            item.request_details.strip()
            for item in parsed_requests.data
            if item.confidence_level == ParsedRequestConfidenceLevel.LOW
            and item.request_details.strip()
        ]

        parsed_summary = [
            f"{item.intent.value}/{item.confidence_level.value}:"
            f"{item.request_items.name!r}x{item.request_items.quantity}"
            for item in parsed_requests.data
        ]
        print(
            "[ExecutionAgent] run start",
            f"session_id={context_object.session_id!r}",
            f"merchant_id={context_object.merchant_id!r}",
            f"has_clover_creds={context_object.clover_creds is not None}",
            f"clover_error={context_object.clover_error!r}",
            f"parsed_request_count={len(parsed_requests.data)}",
            f"parsed_summary={parsed_summary}",
            f"pending_clarification_count={len(pending_clarifications)}",
            f"pending_clarifications={pending_clarifications!r}",
        )
        print(
            "[ExecutionAgent] run tools",
            f"tool_count={len(active_tools)}",
            f"tool_names={[t.name for t in active_tools]}",
            f"max_tool_calls_for_llm={self.max_tool_calls}",
        )

        raw_clarification_and_intent = await getClarificationAndIntent(
            context_object.session_id
        )
        clarification_and_intent = (
            raw_clarification_and_intent
            if raw_clarification_and_intent["success"]
            else None
        )
        print(
            "[ExecutionAgent] run clarification_and_intent",
            f"success={raw_clarification_and_intent['success']}",
            f"found={clarification_and_intent is not None}",
        )

        messages = self._build_messages(
            parsed_requests=parsed_requests,
            context_object=context_object,
            tools=active_tools,
            clarification_and_intent=clarification_and_intent,
        )
        print(
            "[ExecutionAgent] run calling generate_text_with_tools",
            f"message_count={len(messages)}",
            f"model={self.model}",
        )

        async def _call_llm() -> str:
            return await gemini_client.generate_text_with_tools(
                messages,
                function_tools=active_tools,
                temperature=0,
                max_tool_calls=self.max_tool_calls,
                model=self.model,
            )

        agent_reply = await _gemini_service_call_with_retries(
            log_label="[ExecutionAgent]",
            extra_fields=f"model={self.model!r}",
            call=_call_llm,
        )
        agent_questions = extract_questions_from_reply(agent_reply)
        await saveClarificationAndIntent(
            context_object.session_id,
            "" if not pending_clarifications else pending_clarifications,
            parsed_requests.model_dump(mode="json", by_alias=True)["Data"],
            agent_questions=agent_questions,
        )
        reply_one_line = agent_reply.replace("\n", " ")[:400]
        print(
            "[ExecutionAgent] run done",
            f"agent_reply_chars={len(agent_reply)}",
            f"agent_reply_preview={reply_one_line!r}",
            f"actions_executed={tracker.actions_executed!r}",
            f"order_updated={tracker.order_updated}",
            f"agent_questions={agent_questions!r}",
        )

        return ExecutionAgentResult(
            agent_reply=agent_reply,
            session_id=context_object.session_id,
            actions_executed=tracker.actions_executed,
            pending_clarifications=pending_clarifications,
            order_updated=tracker.order_updated,
            agent_questions=agent_questions,
        )

    def _build_messages(
        self,
        *,
        parsed_requests: ParsedRequestsPayload,
        context_object: PreparedExecutionContext,
        tools: Sequence[gemini_client.GeminiFunctionTool],
        clarification_and_intent: dict | None = None,
    ) -> list[LLMMessage]:
        prompt_context = ExecutionAgentPromptContext(
            context_object=context_object.model_dump(mode="json"),
            parsed_requests=parsed_requests.model_dump(mode="json", by_alias=True)[
                "Data"
            ],
            tools=[
                ExecutionAgentToolDescriptor(
                    name=tool.name,
                    description=tool.description,
                ).model_dump(mode="json")
                for tool in tools
            ],
            previous_clarification_and_intent=clarification_and_intent,
        )
        user_content = json.dumps(prompt_context.model_dump(mode="json"), indent=2)
        messages: list[LLMMessage] = [
            {
                "role": "user",
                "content": user_content,
            }
        ]
        if self.system_prompt.strip():
            messages.insert(0, {"role": "system", "content": self.system_prompt})
        system_chars = sum(len(m["content"]) for m in messages if m["role"] == "system")
        user_chars = sum(len(m["content"]) for m in messages if m["role"] == "user")
        print(
            "[ExecutionAgent] _build_messages",
            f"roles={[m['role'] for m in messages]}",
            f"system_chars={system_chars}",
            f"user_json_chars={user_chars}",
            f"tool_descriptors_embedded={len(tools)}",
        )
        return messages

    def build_tools(
        self,
        runtime: ExecutionToolRuntime | None = None,
    ) -> list[gemini_client.GeminiFunctionTool]:
        runtime = runtime or ExecutionToolRuntime(
            context=ExecutionAgentContext(session_id="", merchant_id="")
        )
        print(
            "[ExecutionAgent] build_tools",
            f"session_id={runtime.context.session_id!r}",
            f"merchant_id={runtime.context.merchant_id!r}",
        )
        return self._build_tools(runtime)

    def _build_tools(
        self,
        runtime: ExecutionToolRuntime,
        tracker: ExecutionTracker | None = None,
    ) -> list[gemini_client.GeminiFunctionTool]:
        print(
            "[ExecutionAgent] _build_tools",
            f"session_id={runtime.context.session_id!r}",
            f"merchant_id={runtime.context.merchant_id!r}",
            f"tracker_attached={'yes' if tracker is not None else 'no'}",
            f"has_clover_creds={runtime.context.clover_creds is not None}",
        )

        def _log_tool_call_io(
            tool_name: str, arguments: dict[str, Any], result: dict[str, Any]
        ) -> None:
            """Log the model-facing tool arguments and the tool result dict as JSON."""
            try:
                in_json = json.dumps(
                    arguments, indent=2, ensure_ascii=False, default=str
                )
            except TypeError:
                in_json = repr(arguments)
            try:
                out_json = json.dumps(result, indent=2, ensure_ascii=False, default=str)
            except TypeError:
                out_json = repr(result)
            print(f"[ExecutionAgent] tool={tool_name} INPUT:\n{in_json}")
            print(f"[ExecutionAgent] tool={tool_name} OUTPUT:\n{out_json}")

        async def _validate_requested_item_tool(
            *,
            itemName: str,
            details: str | None = None,
        ) -> dict[str, Any]:
            args: dict[str, Any] = {"itemName": itemName, "details": details}
            if runtime.context.clover_creds is None:
                err = runtime.context.clover_error or "Clover credentials unavailable."
                out: dict[str, Any] = {
                    "exactMatch": None,
                    "candidates": [],
                    "matchConfidence": "none",
                    "itemId": None,
                    "merchantId": None,
                    "available": None,
                    "valid": None,
                    "invalid": None,
                    "asNote": None,
                    "missingRequireChoice": None,
                    "allValid": None,
                    "isModifierOrAddon": None,
                    "classification": None,
                    "closestModifier": None,
                    "error": err,
                }
                _log_tool_call_io("validateRequestedItem", args, out)
                return out
            out = await validateRequestedItem(
                itemName=itemName,
                details=details,
                merchant_id=runtime.context.merchant_id,
                creds=runtime.context.clover_creds,
            )
            _log_tool_call_io("validateRequestedItem", args, out)
            return out

        async def _add_items_to_order_tool(*, items: list[dict]) -> dict[str, Any]:
            args = {"items": items}
            result = await addItemsToOrder(runtime.context.session_id, items, creds=runtime.context.clover_creds)
            if result.get("success") and tracker is not None:
                for added in result.get("addedItems", []):
                    name = str(added.get("name", ""))
                    qty = int(added.get("quantity", 1) or 1)
                    tracker.actions_executed.append(f"added {qty}x {name}")
                tracker.order_updated = True
            _log_tool_call_io("addItemsToOrder", args, result)
            return result

        async def _replace_item_in_order_tool(
            *,
            replacement: dict,
            lineItemId: str | None = None,
            orderPosition: int | None = None,
            itemName: str | None = None,
        ) -> dict[str, Any]:
            args = {
                "replacement": replacement,
                "lineItemId": lineItemId,
                "orderPosition": orderPosition,
                "itemName": itemName,
            }
            result = await replaceItemInOrder(
                runtime.context.session_id,
                replacement,
                lineItemId=lineItemId,
                orderPosition=orderPosition,
                itemName=itemName,
                creds=runtime.context.clover_creds,
            )
            if result.get("success") and tracker is not None:
                removed = str((result.get("removedItem") or {}).get("name", ""))
                added = str((result.get("addedItem") or {}).get("name", ""))
                tracker.actions_executed.append(f"replaced {removed} with {added}")
                tracker.order_updated = True
            _log_tool_call_io("replaceItemInOrder", args, result)
            return result

        async def _remove_item_from_order_tool(*, target: dict) -> dict[str, Any]:
            args = {"target": target}
            result = await removeItemFromOrder(runtime.context.session_id, target, creds=runtime.context.clover_creds)
            if result.get("success") and tracker is not None:
                name = str((result.get("removedItem") or {}).get("name", ""))
                tracker.actions_executed.append(f"removed {name}")
                tracker.order_updated = True
            _log_tool_call_io("removeItemFromOrder", args, result)
            return result

        async def _change_item_quantity_tool(
            *,
            target: dict,
            newQuantity: int,
        ) -> dict[str, Any]:
            args = {"target": target, "newQuantity": newQuantity}
            result = await changeItemQuantity(
                runtime.context.session_id,
                target,
                newQuantity,
                creds=runtime.context.clover_creds,
            )
            if result.get("success") and tracker is not None:
                name = str(result.get("itemName", ""))
                qty = int(result.get("newQuantity", newQuantity) or newQuantity)
                tracker.actions_executed.append(f"changed {name} to {qty}")
                tracker.order_updated = True
            _log_tool_call_io("changeItemQuantity", args, result)
            return result

        async def _update_item_in_order_tool(
            *,
            target: dict,
            updates: dict,
        ) -> dict[str, Any]:
            args = {"target": target, "updates": updates}
            result = await updateItemInOrder(
                runtime.context.session_id, target, updates, creds=runtime.context.clover_creds
            )
            if result.get("success") and tracker is not None:
                name = str(result.get("itemName", ""))
                tracker.actions_executed.append(f"updated {name}")
                tracker.order_updated = True
            _log_tool_call_io("updateItemInOrder", args, result)
            return result

        async def _calc_order_price_tool() -> dict[str, Any]:
            args: dict[str, Any] = {}
            out = await calcOrderPrice(runtime.context.session_id, creds=runtime.context.clover_creds)
            _log_tool_call_io("calcOrderPrice", args, out)
            return out

        async def _confirm_order_tool() -> dict[str, Any]:
            args = {}
            result = await confirmOrder(runtime.context.session_id, creds=runtime.context.clover_creds)
            if result.get("success") and tracker is not None:
                tracker.actions_executed.append("confirmed order")
                tracker.order_updated = True
            _log_tool_call_io("confirmOrder", args, result)
            return result

        async def _cancel_order_tool() -> dict[str, Any]:
            args = {}
            result = await cancelOrder(runtime.context.session_id, creds=runtime.context.clover_creds)
            if result.get("success") and tracker is not None:
                tracker.actions_executed.append("cancelled order")
                tracker.order_updated = True
            _log_tool_call_io("cancelOrder", args, result)
            return result

        async def _get_menu_link_tool() -> dict[str, Any]:
            args = {}
            out = await getMenuLink(
                session_id=runtime.context.session_id,
                merchant_id=runtime.context.merchant_id,
                creds=runtime.context.clover_creds,
            )
            _log_tool_call_io("getMenuLink", args, out)
            return out

        async def _get_items_not_available_today_tool() -> dict[str, Any]:
            args = {}
            out = await getItemsNotAvailableToday(
                merchant_id=runtime.context.merchant_id,
                creds=runtime.context.clover_creds,
            )
            _log_tool_call_io("getItemsNotAvailableToday", args, out)
            return out

        async def _human_intervention_needed_tool(*, reason: str) -> dict[str, Any]:
            args = {"reason": reason}
            out = await humanInterventionNeeded(
                session_id=runtime.context.session_id,
                reason=reason,
                merchant_id=runtime.context.merchant_id or "",
            )
            _log_tool_call_io("humanInterventionNeeded", args, out)
            return out

        async def _get_previous_orders_details_tool(
            *,
            limit: int | None = None,
        ) -> dict[str, Any]:
            eff_limit = limit if limit is not None else 3
            args = {"limit": limit}
            out = await getPreviousOrdersDetails(
                session_id=runtime.context.session_id,
                limit=eff_limit,
            )
            _log_tool_call_io("getPreviousOrdersDetails", args, out)
            return out

        async def _suggested_pickup_time_tool(*, pickup_time_minutes: int) -> dict[str, Any]:
            args = {"pickup_time_minutes": pickup_time_minutes}
            out = await suggestedPickupTime(
                session_id=runtime.context.session_id,
                pickup_time_minutes=pickup_time_minutes,
                merchant_id=runtime.context.merchant_id or "",
            )
            _log_tool_call_io("suggestedPickupTime", args, out)
            return out

        tools_list = [
            gemini_client.GeminiFunctionTool(
                name="validateRequestedItem",
                description=(
                    "Resolve a customer-mentioned item against the live menu, confirm availability, "
                    "validate any requested modifiers, and identify missing required modifier groups — "
                    "all in one call. Use this for ADD_ITEM, MODIFY_ITEM, and REPLACE_ITEM before "
                    "mutating the order."
                ),
                parameters_json_schema=_VALIDATE_REQUESTED_ITEM_PARAMETERS_JSON_SCHEMA,
                handler=_validate_requested_item_tool,
            ),
            gemini_client.GeminiFunctionTool(
                name="addItemsToOrder",
                description="Add one or more resolved menu items to the current order.",
                parameters_json_schema=_ADD_ITEMS_TO_ORDER_PARAMETERS_JSON_SCHEMA,
                handler=_add_items_to_order_tool,
            ),
            gemini_client.GeminiFunctionTool(
                name="replaceItemInOrder",
                description="Replace one existing order item with another resolved menu item.",
                parameters_json_schema=_REPLACE_ITEM_IN_ORDER_PARAMETERS_JSON_SCHEMA,
                handler=_replace_item_in_order_tool,
            ),
            gemini_client.GeminiFunctionTool(
                name="removeItemFromOrder",
                description="Remove an existing item from the current order.",
                parameters_json_schema=_REMOVE_ITEM_FROM_ORDER_PARAMETERS_JSON_SCHEMA,
                handler=_remove_item_from_order_tool,
            ),
            gemini_client.GeminiFunctionTool(
                name="changeItemQuantity",
                description="Change the quantity of an item already in the current order.",
                parameters_json_schema=_CHANGE_ITEM_QUANTITY_PARAMETERS_JSON_SCHEMA,
                handler=_change_item_quantity_tool,
            ),
            gemini_client.GeminiFunctionTool(
                name="updateItemInOrder",
                description="Update modifiers and notes for an existing line item in the current order.",
                parameters_json_schema=_UPDATE_ITEM_IN_ORDER_PARAMETERS_JSON_SCHEMA,
                handler=_update_item_in_order_tool,
            ),
            gemini_client.GeminiFunctionTool(
                name="calcOrderPrice",
                description="Calculate the current order subtotal, tax, and total before confirmation.",
                parameters_json_schema=_NO_ARGUMENTS_JSON_SCHEMA,
                handler=_calc_order_price_tool,
            ),
            gemini_client.GeminiFunctionTool(
                name="confirmOrder",
                description="Submit the current order after explicit customer confirmation.",
                parameters_json_schema=_NO_ARGUMENTS_JSON_SCHEMA,
                handler=_confirm_order_tool,
            ),
            gemini_client.GeminiFunctionTool(
                name="cancelOrder",
                description="Cancel the current order after explicit customer confirmation.",
                parameters_json_schema=_NO_ARGUMENTS_JSON_SCHEMA,
                handler=_cancel_order_tool,
            ),
            gemini_client.GeminiFunctionTool(
                name="getMenuLink",
                description="Return a shareable URL for the full menu. Use when customer asks to see the menu.",
                parameters_json_schema=_GET_MENU_LINK_PARAMETERS_JSON_SCHEMA,
                handler=_get_menu_link_tool,
            ),
            gemini_client.GeminiFunctionTool(
                name="getItemsNotAvailableToday",
                description="Return a list of menu items that are currently unavailable.",
                parameters_json_schema=_GET_ITEMS_NOT_AVAILABLE_TODAY_PARAMETERS_JSON_SCHEMA,
                handler=_get_items_not_available_today_tool,
            ),
            gemini_client.GeminiFunctionTool(
                name="humanInterventionNeeded",
                description=(
                    "MUST be called whenever the customer asks to speak to a human, manager, or staff member, "
                    "OR when the intent is escalation, OR when the situation cannot be resolved automatically. "
                    "Always call this before responding to the customer in these cases."
                ),
                parameters_json_schema=_HUMAN_INTERVENTION_NEEDED_PARAMETERS_JSON_SCHEMA,
                handler=_human_intervention_needed_tool,
            ),
            gemini_client.GeminiFunctionTool(
                name="getPreviousOrdersDetails",
                description="Retrieve order history for the session. Use when customer asks about past orders.",
                parameters_json_schema=_GET_PREVIOUS_ORDERS_DETAILS_PARAMETERS_JSON_SCHEMA,
                handler=_get_previous_orders_details_tool,
            ),
            gemini_client.GeminiFunctionTool(
                name="suggestedPickupTime",
                description=(
                    "MUST be called when the customer suggests a pickup time "
                    "(e.g., 'I'll be there in 30 minutes', 'can I pick up in an hour?'). "
                    "Convert the time to whole minutes and pass as pickup_time_minutes. "
                    "Do NOT call for any other intent."
                ),
                parameters_json_schema=_SUGGESTED_PICKUP_TIME_PARAMETERS_JSON_SCHEMA,
                handler=_suggested_pickup_time_tool,
            ),
        ]
        print(
            "[ExecutionAgent] _build_tools built",
            f"gemini_tool_count={len(tools_list)}",
            f"names={[t.name for t in tools_list]}",
        )
        return tools_list
