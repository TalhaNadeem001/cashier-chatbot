from typing import Literal

from pydantic import BaseModel


class IntentAnalysis(BaseModel):
    state: str
    confidence: Literal["high", "medium", "low"]
    reasoning: str
    alternative: str | None = None
    name: str | None = None


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


class OrderFinalizationIntent(BaseModel):
    intent: Literal["confirm", "modify", "unclear"]


class OrderSupervisionResult(BaseModel):
    is_correct: bool
    corrected_items: list[dict] | None = None
    reasoning: str
