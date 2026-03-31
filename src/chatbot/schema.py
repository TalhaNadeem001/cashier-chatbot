from typing import Literal
from pydantic import BaseModel, Field, field_validator


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


class ModifyItem(BaseModel):
    name: str
    quantity: int | None = None
    modifier: str | None = None
    clear_modifier: bool = False


class SwapItems(BaseModel):
    remove: list[OrderItem]
    add: list[OrderItem]


class BotMessageRequest(BaseModel):
    user_id: str
    message_history: list[Message] | None = None
    latest_message: str = Field(..., max_length=1000)
    order_state: dict | None = None
    previous_state: str | None = None
    previous_food_order_state: str | None = None
    awaiting_order_confirmation: bool = False
    has_pending_clarification: bool = False
    customer_name: str | None = None


class BotMessageResponse(BaseModel):
    chatbot_message: str
    pickup_ping: bool = False
    ping_for_human: bool = False
    order_state: dict | None = None
    previous_state: str | None = None
    previous_food_order_state: str | None = None
    awaiting_order_confirmation: bool = False
    has_pending_clarification: bool = False
    customer_name: str | None = None
