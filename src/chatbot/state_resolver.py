from src.chatbot.chatbot_ai import ChatbotAI
from src.chatbot.constants import ConversationState, FoodOrderState
from src.chatbot.exceptions import AIServiceError
from src.chatbot.schema import Message

# ---------------------------------------------------------------------------
# Transition guard tables
# ---------------------------------------------------------------------------

_ALL_STATES = set(ConversationState)
_ALL_FOOD_STATES = set(FoodOrderState)

VALID_TRANSITIONS: dict[ConversationState | None, set[ConversationState]] = {
    None: _ALL_STATES,
    ConversationState.GREETING: {
        ConversationState.FOOD_ORDER,
        ConversationState.MENU_QUESTION,
        ConversationState.RESTAURANT_QUESTION,
        ConversationState.VAGUE_MESSAGE,
        ConversationState.MISC,
        ConversationState.FAREWELL,
        ConversationState.PICKUP_PING,
    },
    ConversationState.FAREWELL: {
        ConversationState.GREETING,
        ConversationState.MISC,
        ConversationState.FOOD_ORDER,
        ConversationState.MENU_QUESTION,
    },
    ConversationState.FOOD_ORDER: {
        ConversationState.FOOD_ORDER,
        ConversationState.MENU_QUESTION,
        ConversationState.RESTAURANT_QUESTION,
        ConversationState.PICKUP_PING,
        ConversationState.FAREWELL,
        ConversationState.VAGUE_MESSAGE,
        ConversationState.MISC,
    },
    ConversationState.MENU_QUESTION: {
        ConversationState.MENU_QUESTION,
        ConversationState.FOOD_ORDER,
        ConversationState.RESTAURANT_QUESTION,
        ConversationState.FAREWELL,
        ConversationState.VAGUE_MESSAGE,
        ConversationState.MISC,
        ConversationState.PICKUP_PING,
    },
    ConversationState.RESTAURANT_QUESTION: {
        ConversationState.RESTAURANT_QUESTION,
        ConversationState.FOOD_ORDER,
        ConversationState.MENU_QUESTION,
        ConversationState.FAREWELL,
        ConversationState.VAGUE_MESSAGE,
        ConversationState.MISC,
        ConversationState.PICKUP_PING,
    },
    ConversationState.PICKUP_PING: {
        ConversationState.FOOD_ORDER,
        ConversationState.PICKUP_PING,
        ConversationState.FAREWELL,
        ConversationState.MISC,
        ConversationState.VAGUE_MESSAGE,
        ConversationState.MENU_QUESTION,
    },
    ConversationState.VAGUE_MESSAGE: _ALL_STATES - {ConversationState.GREETING},
    ConversationState.MISC: _ALL_STATES - {ConversationState.GREETING},
    ConversationState.FINALIZING_ORDER: {
        ConversationState.FOOD_ORDER,
        ConversationState.FINALIZING_ORDER,
        ConversationState.FAREWELL,
        ConversationState.VAGUE_MESSAGE,
        ConversationState.MISC,
        ConversationState.PICKUP_PING,
    },
}

VALID_FOOD_ORDER_TRANSITIONS: dict[FoodOrderState | None, set[FoodOrderState]] = {
    None: _ALL_FOOD_STATES,
    FoodOrderState.NEW_ORDER: _ALL_FOOD_STATES,
    FoodOrderState.ADD_TO_ORDER: _ALL_FOOD_STATES,
    FoodOrderState.MODIFY_ORDER: _ALL_FOOD_STATES,
    FoodOrderState.REMOVE_FROM_ORDER: _ALL_FOOD_STATES,
    FoodOrderState.SWAP_ITEM: _ALL_FOOD_STATES,
    FoodOrderState.CANCEL_ORDER: {FoodOrderState.NEW_ORDER},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_conversation_state(value: str | None) -> ConversationState | None:
    if not value:
        return None
    try:
        return ConversationState(value.strip().lower())
    except ValueError:
        return None


def _parse_food_order_state(value: str | None) -> FoodOrderState | None:
    if not value:
        return None
    try:
        return FoodOrderState(value.strip().lower())
    except ValueError:
        return None


def _parse_safely_cs(value: str | None) -> ConversationState | None:
    return _parse_conversation_state(value)


def _parse_safely_fo(value: str | None) -> FoodOrderState | None:
    return _parse_food_order_state(value)


# ---------------------------------------------------------------------------
# StateResolver
# ---------------------------------------------------------------------------

class StateResolver:
    def __init__(self, ai: ChatbotAI):
        self._ai = ai

    def _is_valid_transition(
        self,
        previous: ConversationState | None,
        proposed: ConversationState | None,
    ) -> bool:
        if proposed is None:
            return False
        allowed = VALID_TRANSITIONS.get(previous, _ALL_STATES)
        return proposed in allowed

    async def resolve(
        self,
        latest_message: str,
        message_history: list[Message] | None,
        previous_state: ConversationState | None,
    ) -> ConversationState:
        analysis = await self._ai.analyze_intent(
            latest_message=latest_message,
            message_history=message_history,
            previous_state=previous_state.value if previous_state else None,
        )

        proposed = _parse_conversation_state(analysis.state)
        transition_valid = self._is_valid_transition(previous_state, proposed)

        # Fast path
        if analysis.confidence == "high" and transition_valid and proposed is not None:
            return proposed

        # Slow path — independent verifier
        try:
            verification = await self._ai.verify_state(
                latest_message=latest_message,
                message_history=message_history,
                proposed_state=analysis.state,
                previous_state=previous_state.value if previous_state else None,
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
                corrected = _parse_conversation_state(verification.corrected_state)
                if corrected is not None:
                    return corrected

        # Fallback chain: alternative → vague_message
        if analysis.alternative:
            alt = _parse_conversation_state(analysis.alternative)
            if alt is not None:
                return alt

        return ConversationState.VAGUE_MESSAGE


# ---------------------------------------------------------------------------
# FoodOrderStateResolver
# ---------------------------------------------------------------------------

class FoodOrderStateResolver:
    def __init__(self, ai: ChatbotAI):
        self._ai = ai

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
        analysis = await self._ai.analyze_food_order_intent(
            latest_message=latest_message,
            order_state=order_state,
            message_history=message_history,
            previous_food_order_state=(
                previous_food_order_state.value if previous_food_order_state else None
            ),
        )

        proposed = _parse_food_order_state(analysis.state)
        transition_valid = self._is_valid_transition(previous_food_order_state, proposed)

        # Fast path
        if analysis.confidence == "high" and transition_valid and proposed is not None:
            return proposed

        # Slow path — independent verifier
        try:
            verification = await self._ai.verify_food_order_state(
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
