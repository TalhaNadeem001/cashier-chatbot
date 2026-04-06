from typing import Literal
from pydantic import BaseModel, Field, field_validator
from src.chatbot.utils import _parse_food_order_state, _parse_safely
from src.chatbot.constants import ConversationState, FoodOrderState

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


_LONG_MESSAGE_THRESHOLD = 400  # chars; menu dumps are typically 300–800 chars


class BotInteractionRequest(BaseModel):
    user_id: str
    message_history: list[Message] | None = None
    latest_message: str = Field(..., max_length=1000)
    order_state: dict | None = None
    previous_state: ConversationState | None = None
    previous_food_order_state: FoodOrderState | None = None
    customer_name: str | None = None

    @field_validator("previous_state", mode="before")
    @classmethod
    def parse_previous_state(cls, v):
        return _parse_safely(v, ConversationState)

    @field_validator("previous_food_order_state", mode="before")
    @classmethod
    def parse_previous_food_order_state(cls, v):
        if v is None or isinstance(v, FoodOrderState):
            return v
        return _parse_food_order_state(str(v).strip())

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
                msg = msg.model_copy(update={"content": "[discussed menu / detailed response]"})
            result.append(msg)
        return result

class ChatbotResponse(BaseModel):
    chatbot_message: str
    pickup_ping: bool = False
    ping_for_human: bool = False
    order_state: dict | None = None
    previous_state: str | None = None
    previous_food_order_state: str | None = None
    customer_name: str | None = None


class TestResultsSaveRequest(BaseModel):
    content: str  # pre-formatted text to write verbatim


# Backwards-compat aliases (removed after all callers updated)
BotMessageRequest = BotInteractionRequest
BotMessageResponse = ChatbotResponse
