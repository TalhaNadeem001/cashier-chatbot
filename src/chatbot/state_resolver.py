# Re-export shim — will be removed after all callers updated
from src.chatbot.intent.resolver import (
    ConversationStateResolver,
)
from src.chatbot.intent.transitions import VALID_TRANSITIONS

__all__ = [
    "ConversationStateResolver",
    "VALID_TRANSITIONS",
]
