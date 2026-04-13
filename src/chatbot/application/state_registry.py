from collections.abc import Awaitable, Callable

from src.chatbot.api.schema import BotInteractionRequest, ChatbotResponse
from src.chatbot.domain.states import ConversationState
from src.chatbot.exceptions import UnhandledStateError
from src.chatbot.features.ordering.service import FoodOrderingService
from src.chatbot.features.pickup.service import (
    PickupStatusService,
    PickupTimeSuggestionService,
)
from src.chatbot.features.visibility.service import (
    FarewellService,
    GreetingService,
    HumanEscalationService,
    MenuQuestionService,
    MiscService,
    OrderCompletionService,
    OrderReviewService,
    RestaurantQuestionService,
    VagueMessageService,
)

RequestHandler = Callable[[BotInteractionRequest], Awaitable[ChatbotResponse]]


class ConversationStateRegistry:
    def __init__(self) -> None:
        self._handlers: dict[ConversationState, RequestHandler] = {
            ConversationState.GREETING: GreetingService().handle,
            ConversationState.FAREWELL: FarewellService().handle,
            ConversationState.VAGUE_MESSAGE: VagueMessageService().handle,
            ConversationState.RESTAURANT_QUESTION: RestaurantQuestionService().handle,
            ConversationState.MENU_QUESTION: MenuQuestionService().handle,
            ConversationState.FOOD_ORDER: FoodOrderingService().handle,
            ConversationState.PICKUP_PING: PickupStatusService().handle,
            ConversationState.PICKUP_TIME_SUGGESTION: PickupTimeSuggestionService().handle,
            ConversationState.MISC: MiscService().handle,
            ConversationState.HUMAN_ESCALATION: HumanEscalationService().handle,
            ConversationState.ORDER_COMPLETE: OrderCompletionService().handle,
            ConversationState.ORDER_REVIEW: OrderReviewService().handle,
        }

    async def respond_to_message(
        self,
        state: ConversationState,
        request: BotInteractionRequest,
    ) -> ChatbotResponse:
        handler = self._handlers.get(state)
        if handler is None:
            raise UnhandledStateError(f"No handler registered for state: '{state}'")
        return await handler(request)
