from typing import Literal
from src.chatbot.constants import ConversationState, FoodOrderState, ModifierOrderState
from src.chatbot.exceptions import AIServiceError
from src.chatbot.intent.ai_client import (
    analyze_food_order_intent,
    analyze_modifier_order_state,
    detect_user_intent,
    get_customer_name,
    verify_food_order_state,
)
from src.chatbot.intent.transitions import (
    VALID_FOOD_ORDER_TRANSITIONS,
    VALID_TRANSITIONS,
    _ALL_FOOD_STATES,
    _ALL_STATES,
)
from src.chatbot.schema import Message
from src.chatbot.utils import _parse_food_order_state, _parse_modifier_order_state


class ConversationStateResolver:

    async def get_user_name(self, message_history: list[Message] | None, latest_message: str, customer_name: str | None) -> str | None:
        if customer_name:
            return customer_name
        result = await get_customer_name(message_history, latest_message)
        if result.confidence == "high" and result.full_name:
            return result.full_name
        return None

    async def resolve_user_intent(self, latest_message: str, message_history: list[Message] | None, previous_state: ConversationState | None,) -> ConversationState:
        analysis = await detect_user_intent(
            latest_message=latest_message,
            message_history=message_history,
            previous_state=previous_state.value if previous_state else None,
        )
        print("conversation state analysis", analysis)

        # Might be a little too strict, but we don't want to be too lenient with the state transitions.
        if await self._is_valid_intent_transition(previous_state, analysis.state, analysis.confidence):
            return analysis.state

        return ConversationState.VAGUE_MESSAGE
    
    async def _is_valid_intent_transition(self, previous: ConversationState | None, proposed: ConversationState | None, confidence: Literal["high", "medium", "low"] | None) -> bool:
        if confidence != "high":
            return False
        if proposed is ConversationState.HUMAN_ESCALATION:
            return True

        allowed = VALID_TRANSITIONS.get(previous, _ALL_STATES)
        return proposed in allowed


class FoodOrderStateResolver:
    def _is_valid_transition(
        self,
        previous: FoodOrderState | None,
        proposed: FoodOrderState | None,
    ) -> bool:
        if proposed is None:
            return False
        allowed = VALID_FOOD_ORDER_TRANSITIONS.get(previous, _ALL_FOOD_STATES)
        return proposed in allowed

    async def resolve(
        self,
        latest_message: str,
        order_state: dict,
        message_history: list[Message] | None,
        previous_food_order_state: FoodOrderState | None,
    ) -> FoodOrderState:
        analysis = await analyze_food_order_intent(
            latest_message=latest_message,
            order_state=order_state,
            message_history=message_history,
            previous_food_order_state=(
                previous_food_order_state.value if previous_food_order_state else None
            ),
        )
        print("food order analysis", analysis)

        proposed = _parse_food_order_state(analysis.state)
        transition_valid = self._is_valid_transition(previous_food_order_state, proposed)

        # Fast path
        if analysis.confidence == "high" and transition_valid and proposed is not None:
            return proposed

        # Slow path — independent verifier
        try:
            verification = await verify_food_order_state(
                latest_message=latest_message,
                order_state=order_state,
                message_history=message_history,
                proposed_state=analysis.state,
                previous_food_order_state=(
                    previous_food_order_state.value if previous_food_order_state else None
                ),
                transition_valid=transition_valid,
                analysis_reasoning=analysis.reasoning,
            )
        except AIServiceError:
            raise
        except Exception:
            verification = None

        if verification is not None:
            if verification.confirmed and proposed is not None:
                return proposed
            if verification.corrected_state:
                corrected = _parse_food_order_state(verification.corrected_state)
                if corrected is not None:
                    return corrected

        # Fallback chain: alternative → add_to_order
        if analysis.alternative:
            alt = _parse_food_order_state(analysis.alternative)
            if alt is not None:
                return alt

        return FoodOrderState.ADD_TO_ORDER


class ModifierOrderStateResolver:
    async def resolve(
        self,
        latest_message: str,
        order_state: dict,
        message_history: list[Message] | None,
    ) -> ModifierOrderState:
        analysis = await analyze_modifier_order_state(
            latest_message=latest_message,
            order_state=order_state,
            message_history=message_history,
        )

        proposed = _parse_modifier_order_state(analysis.state)

        if analysis.confidence == "high" and proposed is not None:
            return proposed

        if analysis.alternative:
            alt = _parse_modifier_order_state(analysis.alternative)
            if alt is not None:
                return alt

        return ModifierOrderState.NEW_MODIFIER
