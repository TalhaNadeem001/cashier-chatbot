from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from src.chatbot.schema import ModifyItem, OrderItem


class ModifyItemsResult(BaseModel):
    items: list[ModifyItem] = Field(default_factory=list)


class PickupTimeExtraction(BaseModel):
    minutes: int | None = None


class PendingModifierSelections(BaseModel):
    selected_mods: dict[str, Any] = Field(default_factory=dict)


class AmbiguousMatchResolutionPayload(BaseModel):
    confident: bool
    canonical: str | None = None
    clarification_message: str | None = None


class OrderItemsResult(BaseModel):
    items: list[OrderItem] = Field(default_factory=list)
