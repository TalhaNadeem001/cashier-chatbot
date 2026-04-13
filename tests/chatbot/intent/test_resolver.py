from unittest.mock import AsyncMock, patch

import pytest

from src.chatbot.domain.states import ConversationState
from src.chatbot.domain.types import IntentAnalysis
from src.chatbot.features.intent.service import ConversationStateResolver


@pytest.mark.anyio
async def test_resolve_user_intent_accepts_valid_high_confidence_transition() -> None:
    resolver = ConversationStateResolver()
    analysis = IntentAnalysis(
        state=ConversationState.MENU_QUESTION,
        confidence="high",
        reasoning="clear menu question",
    )

    with patch(
        "src.chatbot.features.intent.service.detect_user_intent",
        AsyncMock(return_value=analysis),
    ) as detect_user_intent:
        result = await resolver.resolve_user_intent(
            latest_message="Do you have anything vegetarian?",
            message_history=[],
            previous_state=ConversationState.GREETING,
        )

    assert result is ConversationState.MENU_QUESTION
    detect_user_intent.assert_awaited_once()


@pytest.mark.anyio
async def test_resolve_user_intent_rejects_invalid_transition() -> None:
    resolver = ConversationStateResolver()
    analysis = IntentAnalysis(
        state=ConversationState.GREETING,
        confidence="high",
        reasoning="classifier picked greeting",
    )

    with patch(
        "src.chatbot.features.intent.service.detect_user_intent",
        AsyncMock(return_value=analysis),
    ):
        result = await resolver.resolve_user_intent(
            latest_message="hello again",
            message_history=[],
            previous_state=ConversationState.FOOD_ORDER,
        )

    assert result is ConversationState.VAGUE_MESSAGE
