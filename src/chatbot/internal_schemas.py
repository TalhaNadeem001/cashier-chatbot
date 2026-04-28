from typing import Any, Literal
from pydantic import BaseModel, Field, field_validator
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


class ModifierOrderIntentAnalysis(BaseModel):
    state: str
    confidence: Literal["high", "medium", "low"]
    reasoning: str
    alternative: str | None = None


class CustomerNameAnalysis(BaseModel):
    full_name: str | None = None
    confidence: Literal["high", "medium", "low"]


class ModifierAssignmentResult(BaseModel):
    items: list[dict]


class MenuMatchIssue(BaseModel):
    kind: Literal["ambiguous", "not_found"]
    requested_name: str
    candidates: list[str] = Field(default_factory=list)
    clarification_message: str | None = None


class ModifierValidationIssue(BaseModel):
    item_name: str
    invalid_modifier: str
    allowed_options: list[str] = Field(default_factory=list)


class ClosestModifierResolution(BaseModel):
    status: Literal["match", "no_match"]
    canonical_modifier: str | None = None
    reasoning: str | None = None


ModifierAddonKind = Literal[
    "quantity_variation",
    "cooking_preference",
    "ingredient_variation",
    "not_addon",
]


class ClosestModifierReference(BaseModel):
    modifierId: str
    name: str


class ModifierAddonCheckResult(BaseModel):
    isModifierOrAddon: bool
    classification: ModifierAddonKind | None = None
    closestModifier: ClosestModifierReference | None = None
    suggestedNote: str | None = None


class OrderFollowUpRequirement(BaseModel):
    kind: Literal[
        "burger_patties", "wings_quantity", "wings_flavor", "wings_flavor_limit"
    ]
    item_name: str
    details: dict[str, Any] = Field(default_factory=dict)


class ComboEvent(BaseModel):
    kind: Literal["attached", "removed"]
    combo_name: str
    combo_price: int | None = None


class ComboApplicationResult(BaseModel):
    order_state: dict
    combo_event: ComboEvent | None = None


class OrderValidationResult(BaseModel):
    items: list[dict] = Field(default_factory=list)
    invalid_modifiers: list[ModifierValidationIssue] = Field(default_factory=list)
    follow_up_requirements: list[OrderFollowUpRequirement] = Field(default_factory=list)


class ResolvedModifierItem(BaseModel):
    modifierId: str
    name: str
    groupId: str
    groupName: str
    price: int = 0


class ModifierResolutionResult(BaseModel):
    resolved: list[ResolvedModifierItem] = Field(default_factory=list)
    to_remove: list[str] = Field(default_factory=list)  # modifier IDs to remove from existing
    as_note: list[str] = Field(default_factory=list)
    unresolvable: list[str] = Field(default_factory=list)


class OrderProcessingOutcome(BaseModel):
    previous_order: dict = Field(default_factory=dict)
    accepted_order: dict = Field(default_factory=dict)
    menu_match_issues: list[MenuMatchIssue] = Field(default_factory=list)
    invalid_modifiers: list[ModifierValidationIssue] = Field(default_factory=list)
    follow_up_requirements: list[OrderFollowUpRequirement] = Field(default_factory=list)
    combo_event: ComboEvent | None = None
    confirmation_resolved: bool = False
    order_empty: bool = False
