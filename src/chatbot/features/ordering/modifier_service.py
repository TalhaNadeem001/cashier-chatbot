from typing import Any

from src.chatbot.api.schema import BotInteractionRequest, ChatbotResponse
from src.chatbot.domain.states import ModifierOrderState
from src.chatbot.exceptions import UnhandledStateError
from src.chatbot.features.intent.ai_client import (
    assign_item_modifiers,
    remove_item_modifiers,
    swap_item_modifiers,
)
from src.chatbot.features.intent.service import ModifierOrderStateResolver
from src.chatbot.features.ordering.combo_service import detect_and_attach_combo
from src.chatbot.features.ordering.item_detection_service import validate_order_items
from src.chatbot.features.ordering.service import (
    _enrich_items_with_resolved_mods,
    _enrich_order_state_with_prices,
)
from src.chatbot.features.ordering.utils import extract_items, merge_modifier_items


class ModifierStateHandler:
    def __init__(self) -> None:
        self._handlers = {
            ModifierOrderState.ADD_MODIFIER: self._handle_add_modifier,
            ModifierOrderState.REMOVE_MODIFIER: self._handle_remove_modifier,
            ModifierOrderState.SWAP_MODIFIER: self._handle_swap_modifier,
            ModifierOrderState.CANCEL_MODIFIER: self._handle_cancel_modifier,
            ModifierOrderState.NO_MODIFIER: self._handle_no_modifier,
        }

    async def handle(
        self,
        request: BotInteractionRequest,
        food_response: ChatbotResponse | None = None,
    ) -> ChatbotResponse:
        resolver = ModifierOrderStateResolver()
        modifier_state = await resolver.resolve(
            latest_message=request.latest_message,
            order_state=request.order_state or {},
            message_history=request.message_history,
        )

        handler = self._handlers.get(modifier_state)
        if handler is None:
            raise UnhandledStateError(
                f"No handler registered for modifier order state: '{modifier_state}'"
            )

        response = await handler(request, food_response)
        if response.order_state:
            enriched = await _enrich_order_state_with_prices(response.order_state)
            response = response.model_copy(update={"order_state": enriched})
        return response

    async def _handle_add_modifier(
        self,
        request: BotInteractionRequest,
        food_response: ChatbotResponse | None = None,
    ) -> ChatbotResponse:
        return await self._apply_modifier_assignment(request, food_response, assign_item_modifiers)

    async def _handle_remove_modifier(
        self,
        request: BotInteractionRequest,
        food_response: ChatbotResponse | None = None,
    ) -> ChatbotResponse:
        return await self._apply_modifier_assignment(
            request,
            food_response,
            remove_item_modifiers,
        )

    async def _handle_swap_modifier(
        self,
        request: BotInteractionRequest,
        food_response: ChatbotResponse | None = None,
    ) -> ChatbotResponse:
        return await self._apply_modifier_assignment(request, food_response, swap_item_modifiers)

    async def _handle_cancel_modifier(
        self,
        request: BotInteractionRequest,
        food_response: ChatbotResponse | None = None,
    ) -> ChatbotResponse:
        order_state = dict(request.order_state or {})
        items = [dict(item) for item in order_state.get("items", [])]
        for item in items:
            item["modifier"] = ""
        order_state["items"] = items
        base = (food_response or ChatbotResponse(chatbot_message="")).model_copy(
            update={"order_state": order_state}
        )
        return await self._detect_special_cases(base)

    async def _handle_no_modifier(
        self,
        request: BotInteractionRequest,
        food_response: ChatbotResponse | None = None,
    ) -> ChatbotResponse:
        base = (food_response or ChatbotResponse(chatbot_message="")).model_copy(
            update={"order_state": request.order_state}
        )
        return await self._detect_special_cases(base)

    async def _apply_modifier_assignment(
        self,
        request: BotInteractionRequest,
        food_response: ChatbotResponse | None,
        client: Any,
    ) -> ChatbotResponse:
        items = await extract_items(request)
        result = await client(
            latest_message=request.latest_message,
            items=items,
            message_history=request.message_history,
        )
        merged = await merge_modifier_items(result.items)
        base = (food_response or ChatbotResponse(chatbot_message="")).model_copy(
            update={"order_state": {"items": merged}}
        )
        return await self._detect_special_cases(base)

    async def _detect_special_cases(self, response: ChatbotResponse) -> ChatbotResponse:
        order_state = response.order_state
        if not order_state:
            return response

        items = order_state.get("items", [])
        response = await detect_and_attach_combo(items, response)
        response = await validate_order_items(items, response)
        enriched_items = await _enrich_items_with_resolved_mods(
            (response.order_state or {}).get("items", [])
        )
        return response.model_copy(
            update={"order_state": {**(response.order_state or {}), "items": enriched_items}}
        )
