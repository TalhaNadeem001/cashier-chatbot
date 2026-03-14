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


class BotMessageRequest(BaseModel):
    user_id: str
    message_history: list[Message] | None = None
    latest_message: str = Field(..., max_length=1000)
    order_state: dict | None = None


class BotMessageResponse(BaseModel):
    chatbot_message: str
    pickup_ping: bool = False
    order_state: dict | None = None
