import asyncio
from copy import deepcopy
import re

from src.chatbot.infrastructure.summarizer import compress_history_if_needed
from src.chatbot.constants import ConversationState
from src.chatbot.intent.resolver import ConversationStateResolver
from src.chatbot.schema import BotInteractionRequest, ChatbotResponse
from src.chatbot.visibility.handlers import StateHandlerFactory

_USER_MESSAGE_LOCKS: dict[str, asyncio.Lock] = {}
_SESSION_ORDER_STATE: dict[str, dict] = {}
_GLOBAL_MESSAGE_LOCK = asyncio.Lock()
_NAME_PATTERNS = (
    re.compile(r"\border\s+for\s+([a-z][a-z\s'.-]{0,30})\b", re.IGNORECASE),
    re.compile(r"\b(?:it'?s|its)\s+for\s+([a-z][a-z\s'.-]{0,30})\b", re.IGNORECASE),
    re.compile(r"\b(?:name\s+is|under\s+the\s+name)\s+([a-z][a-z\s'.-]{0,30})\b", re.IGNORECASE),
    re.compile(r"\b(?:hi|hey|hello)\s+([a-z]+(?:\s+[a-z]+){0,2})\s+here\b", re.IGNORECASE),
    re.compile(r"\b([a-z]+(?:\s+[a-z]+){1,2})\s+here\b", re.IGNORECASE),
)
_INVALID_NAME_TOKENS = {
    "need",
    "want",
    "make",
    "order",
    "please",
    "hello",
    "hey",
    "hi",
    "here",
    "to",
    "an",
    "a",
    "for",
    "with",
    "and",
}


class ChatReplyService:
    def __init__(self):
        self.conversation_engine = StateHandlerFactory()
        self.chatbot = ConversationStateResolver()

    async def interpret_and_respond(self, Conversation: BotInteractionRequest) -> ChatbotResponse:
        async with _GLOBAL_MESSAGE_LOCK:
            async with self._get_user_lock(Conversation.user_id):
                print("[chat] request.latest_message:", Conversation.latest_message)
                print("[chat] request.previous_state:", Conversation.previous_state)
                print("[chat] request.order_state:", Conversation.order_state)
                effective_order_state = self._resolve_effective_order_state(
                    user_id=Conversation.user_id,
                    incoming_order_state=Conversation.order_state,
                )
                working_request = Conversation.model_copy(
                    update={"order_state": deepcopy(effective_order_state)}
                )
                print("[chat] effective_order_state:", working_request.order_state)

                message_history = await compress_history_if_needed(
                    user_id=working_request.user_id,
                    message_history=working_request.message_history,
                )
                print("[chat] compressed_history_count:", len(message_history or []))

                state = await self.chatbot.resolve_user_intent(
                    latest_message=working_request.latest_message,
                    message_history=message_history,
                    previous_state=working_request.previous_state,
                )
                print("[chat] resolved_conversation_state:", state)

                response = await self.conversation_engine.respond_to_message(state, working_request)
                self._persist_name_from_latest_message(working_request, response)
                if self._should_request_customer_name(state, working_request, response):
                    response.chatbot_message = (
                        f"{response.chatbot_message} Also, what name should I put on this order?"
                    )
                response.previous_state = state.value
                self._update_session_order_state(
                    user_id=working_request.user_id,
                    updated_order_state=response.order_state,
                )
                print("[chat] response.chatbot_message:", response.chatbot_message)
                print("[chat] response.order_state:", response.order_state)
                return response

    def _should_request_customer_name(
        self,
        state: ConversationState,
        request: BotInteractionRequest,
        response: ChatbotResponse,
    ) -> bool:
        if state not in {
            ConversationState.FOOD_ORDER,
            ConversationState.ORDER_REVIEW,
            ConversationState.ORDER_COMPLETE,
        }:
            return False
        if self._has_known_customer_name(request, response):
            return False
        if self._message_already_requests_name(response.chatbot_message):
            return False
        return True

    def _has_known_customer_name(
        self,
        request: BotInteractionRequest,
        response: ChatbotResponse,
    ) -> bool:
        if (request.customer_name or "").strip():
            return True
        if (response.customer_name or "").strip():
            return True
        request_order_state = request.order_state or {}
        response_order_state = response.order_state or {}
        if str(request_order_state.get("customer_label", "")).strip():
            return True
        if str(response_order_state.get("customer_label", "")).strip():
            return True
        return False

    def _message_already_requests_name(self, message: str) -> bool:
        lowered = (message or "").lower()
        return (
            "what's your name" in lowered
            or "what name should i put" in lowered
            or "should i write the order name down as" in lowered
        )

    def _get_user_lock(self, user_id: str) -> asyncio.Lock:
        lock = _USER_MESSAGE_LOCKS.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            _USER_MESSAGE_LOCKS[user_id] = lock
        return lock

    def _resolve_effective_order_state(
        self,
        user_id: str,
        incoming_order_state: dict | None,
    ) -> dict:
        stored = _SESSION_ORDER_STATE.get(user_id)
        if stored is None:
            return deepcopy(incoming_order_state or {})
        # Server-side session state is source-of-truth once established; this avoids stale
        # client snapshots overwriting recent cart updates during rapid message bursts.
        return deepcopy(stored)

    def _update_session_order_state(self, user_id: str, updated_order_state: dict | None) -> None:
        _SESSION_ORDER_STATE[user_id] = deepcopy(updated_order_state or {})

    def _persist_name_from_latest_message(
        self,
        request: BotInteractionRequest,
        response: ChatbotResponse,
    ) -> None:
        if self._has_known_customer_name(request, response):
            return
        candidate = self._extract_name_candidate(request.latest_message)
        if not candidate:
            return
        order_state = deepcopy(response.order_state or request.order_state or {})
        order_state["customer_label"] = candidate
        response.order_state = order_state

    def _extract_name_candidate(self, message: str) -> str | None:
        text = str(message or "").strip()
        if not text:
            return None
        for pattern in _NAME_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            label = " ".join(match.group(1).split()).strip(" .,!?:;")
            if label and self._looks_like_person_name(label):
                return label
        return None

    def _looks_like_person_name(self, value: str) -> bool:
        tokens = [token for token in re.split(r"\s+", value.lower()) if token]
        if not tokens:
            return False
        if any(token in _INVALID_NAME_TOKENS for token in tokens):
            return False
        return True
