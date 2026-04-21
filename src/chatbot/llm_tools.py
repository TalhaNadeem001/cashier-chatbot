from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class GeminiFunctionTool:
    name: str
    description: str
    parameters_json_schema: dict[str, Any]
    handler: Callable[..., Awaitable[dict[str, Any]]]
