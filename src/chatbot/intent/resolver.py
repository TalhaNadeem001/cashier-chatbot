from typing import Literal
from src.chatbot.constants import ConversationState
from src.chatbot.intent.ai_client import (
    detect_user_intent,
    get_customer_name,
)
from src.chatbot.intent.transitions import (
    VALID_TRANSITIONS,
    _ALL_STATES,
)
from src.chatbot.schema import Message


class ConversationStateResolver:

    async def get_user_name(self, message_history: list[Message] | None, latest_message: str, customer_name: str | None) -> str | None:
        if customer_name:
            return customer_name
        result = await get_customer_name(message_history, latest_message)
        if result.confidence == "high" and result.full_name:
            return result.full_name
        return None

    async def resolve_user_intent(self, latest_message: str, message_history: list[Message] | None, previous_state: ConversationState | None,) -> ConversationState:
        print("[intent] latest_message:", latest_message)
        print("[intent] previous_state:", previous_state)
        analysis = await detect_user_intent(
            latest_message=latest_message,
            message_history=message_history,
            previous_state=previous_state.value if previous_state else None,
        )
        print("[intent] conversation_state_analysis:", analysis)

        # Might be a little too strict, but we don't want to be too lenient with the state transitions.
        is_valid = await self._is_valid_intent_transition(previous_state, analysis.state, analysis.confidence)
        print("[intent] transition_valid:", is_valid)
        if is_valid:
            print("[intent] resolved_state:", analysis.state)
            return analysis.state

        print("[intent] fallback_state:", ConversationState.VAGUE_MESSAGE)
        return ConversationState.VAGUE_MESSAGE
    
    async def _is_valid_intent_transition(self, previous: ConversationState | None, proposed: ConversationState | None, confidence: Literal["high", "medium", "low"] | None) -> bool:
        if confidence != "high":
            return False
        if proposed is ConversationState.HUMAN_ESCALATION:
            return True

        allowed = VALID_TRANSITIONS.get(previous, _ALL_STATES)
        return proposed in allowed
