from typing import Literal
from pydantic import BaseModel, field_validator
from src.chatbot.constants import ConversationState
from src.chatbot.utils import _parse_conversation_state

class IntentAnalysis(BaseModel):
    state: ConversationState
    confidence: Literal["high", "medium", "low"]
    reasoning: str
    alternative: str | None = None
    name: str | None = None

    @field_validator("state", mode="before")
    @classmethod
    def parse_state(cls, value: str | None) -> str | None:
        parsed = _parse_conversation_state(value)
        return parsed.value if parsed else None


class StateVerification(BaseModel):
    confirmed: bool
    corrected_state: str | None = None


class FoodOrderIntentAnalysis(BaseModel):
    state: str
    confidence: Literal["high", "medium", "low"]
    reasoning: str
    alternative: str | None = None


class FoodOrderStateVerification(BaseModel):
    confirmed: bool
    corrected_state: str | None = None


class ModifierStateIntentAnalysis(BaseModel):
    state: str
    confidence: Literal["high", "medium", "low"]
    reasoning: str
    alternative: str | None = None


class ModifierStateVerification(BaseModel):
    confirmed: bool
    corrected_state: str | None = None


class OrderFinalizationIntent(BaseModel):
    intent: Literal["confirm", "modify", "unclear"]


class OrderSupervisionResult(BaseModel):
    is_correct: bool
    corrected_items: list[dict] | None = None
    reasoning: str


class ModifierJourneyAnalysis(BaseModel):
    intent: Literal["providing_selection", "not_providing_selection"]
    confidence: Literal["high", "medium", "low"]
    reasoning: str


class CustomerNameAnalysis(BaseModel):
    full_name: str | None = None
    confidence: Literal["high", "medium", "low"]
