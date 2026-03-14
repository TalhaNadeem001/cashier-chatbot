from src.chatbot.chatbot_ai import ChatbotAI
from src.chatbot.constants import FoodOrderState

from src.chatbot.exceptions import UnhandledStateError
from src.chatbot.schema import BotMessageRequest, BotMessageResponse


class FoodOrderHandlerFactory:
    def __init__(self, ai: ChatbotAI):
        self._ai = ai
        self._handlers = {
            FoodOrderState.NEW_ORDER: self._handle_new_order,
            FoodOrderState.ADD_TO_ORDER: self._handle_add_to_order,
            FoodOrderState.MODIFY_ORDER: self._handle_modify_order,
            FoodOrderState.REMOVE_FROM_ORDER: self._handle_remove_from_order,
            FoodOrderState.CANCEL_ORDER: self._handle_cancel_order,
        }

    async def handle(self, request: BotMessageRequest) -> BotMessageResponse:
        if not request.order_state:
            return await self._handle_new_order(request)

        food_order_state = await self._ai.determine_food_order_state(
            latest_message=request.latest_message,
            order_state=request.order_state,
            message_history=request.message_history,
        )

        handler = self._handlers.get(food_order_state)
        if handler is None:
            raise UnhandledStateError(f"No handler registered for food order state: '{food_order_state}'")

        return await handler(request)

    async def _handle_new_order(self, request: BotMessageRequest) -> BotMessageResponse:
        items = await self._ai.extract_order_items(
            latest_message=request.latest_message,
            message_history=request.message_history,
        )
        order_state = {"items": [item.model_dump() for item in items]}
        return BotMessageResponse(chatbot_message=FoodOrderState.NEW_ORDER, order_state=order_state)

    async def _handle_add_to_order(self, request: BotMessageRequest) -> BotMessageResponse:
        pass

    async def _handle_modify_order(self, request: BotMessageRequest) -> BotMessageResponse:
        pass

    async def _handle_remove_from_order(self, request: BotMessageRequest) -> BotMessageResponse:
        pass

    async def _handle_cancel_order(self, request: BotMessageRequest) -> BotMessageResponse:
        pass
