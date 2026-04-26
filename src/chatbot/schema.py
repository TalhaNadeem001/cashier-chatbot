from enum import Enum
from typing import Any, Literal

from dataclasses import dataclass
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.chatbot.constants import ConversationState


@dataclass(frozen=True, slots=True)
class ParsingAgentPrompts:
    identity_prompt: str
    input_you_receive_prompt: str
    output_format_prompt: str
    intent_labels_prompt: str
    parsing_rules_prompt: str
    few_shot_examples_prompt: str
    final_reminders_prompt: str
    internal_validation_prompt: str
    strict_retry_prompt: str


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(..., max_length=2000)

    @field_validator("content")
    @classmethod
    def no_scripts(cls, v: str) -> str:
        if "<script>" in v.lower():
            raise ValueError("Scripts not allowed")
        return v


class OrderItem(BaseModel):
    name: str
    quantity: int
    modifier: str | None = None
    selected_mods: dict[str, str | list[str]] | None = None
    item_id: str | None = None
    resolved_mods: list[dict] | None = None


class ModifyItem(BaseModel):
    name: str
    quantity: int | None = None
    modifier: str | None = None
    clear_modifier: bool = False
    selected_mods: dict[str, str | list[str]] | None = None
    clear_selected_mods: bool = False


class SwapItems(BaseModel):
    remove: list[OrderItem]
    add: list[OrderItem]


class AddItemsResult(BaseModel):
    new_items: list[OrderItem]


class OrderDeltaResult(BaseModel):
    items: list[OrderItem]


_LONG_MESSAGE_THRESHOLD = 400  # chars; menu dumps are typically 300–800 chars


class BotInteractionRequest(BaseModel):
    user_id: str
    message_history: list[Message] | None = None
    latest_message: str = Field(..., max_length=1000)
    order_state: dict | None = None
    previous_state: ConversationState | None = None
    customer_name: str | None = None

    @field_validator("previous_state", mode="before")
    @classmethod
    def parse_previous_state(cls, v):
        from src.chatbot.utils import _parse_safely

        return _parse_safely(v, ConversationState)

    @field_validator("message_history", mode="before")
    @classmethod
    def collapse_long_messages(cls, v):
        if not v:
            return v
        result = []
        for msg in v:
            if isinstance(msg, dict):
                if len(msg.get("content", "")) > _LONG_MESSAGE_THRESHOLD:
                    msg = {**msg, "content": "[discussed menu / detailed response]"}
            elif hasattr(msg, "content") and len(msg.content) > _LONG_MESSAGE_THRESHOLD:
                msg = msg.model_copy(
                    update={"content": "[discussed menu / detailed response]"}
                )
            result.append(msg)
        return result


class ChatbotResponse(BaseModel):
    chatbot_message: str
    pickup_ping: bool = False
    ping_for_human: bool = False
    order_state: dict | None = None
    previous_state: str | None = None
    customer_name: str | None = None
    pickup_time_suggestion: int | None = None
    pickup_time_suggestion_timestamp: str | None = None


class ChatbotV2MessageRequest(BaseModel):
    user_message: str 
    session_id: str 
    merchant_id: str | None = None
    phone_number: str | None = None


class ChatbotV2MessageResponse(BaseModel):
    system_response: str
    session_id: str


class TestResultsSaveRequest(BaseModel):
    content: str  # pre-formatted text to write verbatim


class ClearSessionRequest(BaseModel):
    session_id: str


class ParsedRequestIntent(str, Enum):
    ADD_ITEM = "add_item"
    MODIFY_ITEM = "modify_item"
    REPLACE_ITEM = "replace_item"
    REMOVE_ITEM = "remove_item"
    CHANGE_ITEM_NUMBER = "change_item_number"
    CONFIRM_ORDER = "confirm_order"
    CANCEL_ORDER = "cancel_order"
    ORDER_QUESTION = "order_question"
    MENU_QUESTION = "menu_question"
    RESTAURANT_QUESTION = "restaurant_question"
    ESCALATION = "escalation"
    PICKUPTIME_QUESTION = "pickuptime_question"
    GREETING = "greeting"
    INTRODUCE_NAME = "introduce_name"
    OUTSIDE_AGENT_SCOPE = "outside_agent_scope"


class ParsedRequestConfidenceLevel(str, Enum):
    HIGH = "high"
    LOW = "low"


class QAPair(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str
    answer: str | None


class ParsedRequestItems(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    quantity: int
    details: str


class ParsedRequestItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    intent: ParsedRequestIntent = Field(alias="Intent")
    confidence_level: ParsedRequestConfidenceLevel = Field(alias="Confidence_level")
    request_items: ParsedRequestItems = Field(alias="Request_items")
    request_details: str = Field(alias="Request_details")


class ModifiedQueueEntry(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    entry_id: str = Field(alias="EntryId")
    qa: list[QAPair] = Field(alias="QA")


class ParsedRequestsPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    data: list[ParsedRequestItem] = Field(alias="Data")
    modified_entries: list[ModifiedQueueEntry] = Field(alias="ModifiedEntries")


class IntentQueueEntry(BaseModel):
    entry_id: str
    status: Literal["pending", "need_clarification"] = "pending"
    parsed_item: dict[str, Any]
    qa: list[QAPair] = Field(default_factory=list)


class CurrentOrderLineItem(BaseModel):
    line_item_id: str
    name: str
    quantity: int
    price: int


class CurrentOrderDetails(BaseModel):
    order_id: str
    line_items: list[CurrentOrderLineItem]
    order_total: int
    raw_error: str | None = None


class ParsingAgentContext(BaseModel):
    session_id: str
    merchant_id: str | None = None
    current_order_details: CurrentOrderDetails
    most_recent_message: str
    latest_k_messages_by_customer: list[str]
    unfulfilled_queue: list[dict[str, Any]] = []


class ParsingAgentPromptContext(BaseModel):
    current_order_details: dict[str, Any]
    most_recent_message_by_customer: str
    latest_k_messages_by_customer: list[str]
    unfulfilled_queue: list[dict[str, Any]] = []


class ParsingAgentResult(BaseModel):
    context: ParsingAgentContext
    parsed_requests: ParsedRequestsPayload


class ExecutionAgentContext(BaseModel):
    session_id: str
    merchant_id: str | None = None
    original_merchant_id: str | None = None
    clover_creds: dict[str, Any] | None = None
    clover_error: str | None = None
    phone_number: str | None = None


class PreparedExecutionContext(BaseModel):
    session_id: str
    merchant_id: str | None = None
    original_merchant_id: str | None = None
    latest_customer_message: str
    current_order_details: CurrentOrderDetails
    latest_k_messages_by_customer: list[str]
    clover_creds: dict[str, Any] | None = None
    clover_error: str | None = None
    is_order_confirmed: bool = False
    phone_number: str | None = None


class ExecutionAgentToolDescriptor(BaseModel):
    name: str
    description: str


class ExecutionAgentPromptContext(BaseModel):
    context_object: dict[str, Any]
    intent: dict[str, Any]
    qa: list[dict[str, Any]]
    tools: list[dict[str, Any]]


class ExecutionAgentSingleResult(BaseModel):
    success: bool
    reply: str
    clarification_questions: list[str] = Field(default_factory=list)
    actions_executed: list[str] = Field(default_factory=list)
    order_updated: bool = False


# Backwards-compat aliases (removed after all callers updated)
BotMessageRequest = BotInteractionRequest
BotMessageResponse = ChatbotResponse
