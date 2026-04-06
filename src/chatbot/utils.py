# Helper functions for chatbot
from src.chatbot.constants import ConversationState, FoodOrderState, ModifierOrderState

def _parse_safely(value: str | None, enum_cls):
    if not value:
        return None
    try:
        return enum_cls(value.strip().lower())
    except ValueError:
        return None

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
    normalized = value.strip().lower()
    if normalized == "add_modifiers":
        normalized = FoodOrderState.ADDING_MODIFIERS.value
    try:
        return FoodOrderState(normalized)
    except ValueError:
        return None


def _parse_modifier_order_state(value: str | None) -> ModifierOrderState | None:
    if not value:
        return None
    try:
        return ModifierOrderState(value.strip().lower())
    except ValueError:
        return None


