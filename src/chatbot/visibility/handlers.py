from src.chatbot.application.state_registry import ConversationStateRegistry


class StateHandlerFactory:
    def __init__(self) -> None:
        self._registry = ConversationStateRegistry()

    async def respond_to_message(self, state, request):
        return await self._registry.respond_to_message(state, request)


__all__ = ["StateHandlerFactory"]
