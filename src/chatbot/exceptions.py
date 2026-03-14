class AIServiceError(Exception):
    """Raised when the OpenAI API call fails."""


class InvalidConversationStateError(AIServiceError):
    """Raised when the AI returns a state value that does not match any ConversationState."""


class UnhandledStateError(Exception):
    """Raised when no handler is registered for a given ConversationState."""
