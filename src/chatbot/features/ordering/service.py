import asyncio

from src.chatbot.api.schema import BotInteractionRequest, ChatbotResponse
from src.chatbot.domain.states import FoodOrderState
from src.chatbot.exceptions import UnhandledStateError
from src.chatbot.features.clarification.fuzzy_matcher import FuzzyMatcher, _MatchResult
from src.chatbot.features.clarification.service import (
    ClarificationBuilder,
    merge_items,
    remove_items,
)
from src.chatbot.features.intent.service import FoodOrderStateResolver
from src.menu.infrastructure.repository import menu_repository


async def _enrich_items_with_resolved_mods(items: list[dict]) -> list[dict]:
    enriched: list[dict] = []
    for item in items:
        current = dict(item)
        if not current.get("item_id"):
            current["item_id"] = menu_repository.get_item_id(current.get("name", ""))
        selected_mods = current.get("selected_mods")
        modifier_str = current.get("modifier") or ""
        if selected_mods:
            current["resolved_mods"] = menu_repository.resolve_mod_ids(
                current.get("name", ""),
                selected_mods,
            )
        elif modifier_str.strip():
            current["resolved_mods"] = menu_repository.resolve_mod_ids_from_string(
                current.get("name", ""),
                modifier_str,
            )
        enriched.append(current)
    return enriched


async def _enrich_order_state_with_prices(order_state: dict) -> dict:
    items = order_state.get("items", [])
    enriched: list[dict] = []
    order_total = 0
    for item in items:
        current = dict(item)
        definition = menu_repository.get_item_definition(current.get("name", ""))
        unit_price = definition.get("price") if definition else None
        if unit_price is not None:
            quantity = current.get("quantity", 1)
            current["unit_price"] = unit_price
            current["item_total"] = unit_price * quantity
            order_total += current["item_total"]
        enriched.append(current)
    return {**order_state, "items": enriched, "order_total": order_total}


def _append_not_found_menu_messages(messages: list[str], not_found: list[_MatchResult]) -> None:
    custom_messages = list(
        dict.fromkeys(message for result in not_found if (message := result.clarification_message))
    )
    messages.extend(custom_messages)
    generic_results = [result for result in not_found if not result.clarification_message]
    if generic_results:
        names = ", ".join(f'"{result.item.name}"' for result in generic_results)
        messages.append(f"Sorry, I couldn't find {names} on our menu.")


class FoodOrderingService:
    def __init__(self) -> None:
        self._order_handler = OrderStateHandler()

    async def handle(self, request: BotInteractionRequest) -> ChatbotResponse:
        from src.chatbot.features.ordering.modifier_service import ModifierStateHandler

        food_response = await self._order_handler.handle(request)
        updated_request = request.model_copy(update={"order_state": food_response.order_state})
        return await ModifierStateHandler().handle(updated_request, food_response)


class OrderStateHandler:
    def __init__(self) -> None:
        from src.chatbot.features.ordering.extractor import OrderExtractor

        self._extractor = OrderExtractor()
        self._matcher = FuzzyMatcher()
        self._builder = ClarificationBuilder()
        self._handlers = {
            FoodOrderState.NEW_ORDER: self._handle_new_order,
            FoodOrderState.ADD_TO_ORDER: self._handle_add_to_order,
            FoodOrderState.REMOVE_FROM_ORDER: self._handle_remove_from_order,
            FoodOrderState.SWAP_ITEM: self._handle_swap_item,
            FoodOrderState.CANCEL_ORDER: self._handle_cancel_order,
            FoodOrderState.ORDER_MODIFIER_REQUEST: self._handle_order_modifier_request,
        }

    async def handle(self, request: BotInteractionRequest) -> ChatbotResponse:
        resolver = FoodOrderStateResolver()
        food_order_state = await resolver.resolve(
            latest_message=request.latest_message,
            order_state=request.order_state,
            message_history=request.message_history,
            previous_food_order_state=request.previous_food_order_state,
        )

        handler = self._handlers.get(food_order_state)
        if handler is None:
            raise UnhandledStateError(
                f"No handler registered for food order state: '{food_order_state}'"
            )
        response = await handler(request)
        response.previous_food_order_state = food_order_state.value
        if response.order_state:
            enriched = await _enrich_order_state_with_prices(response.order_state)
            response = response.model_copy(update={"order_state": enriched})
        return response

    async def _handle_new_order(self, request: BotInteractionRequest) -> ChatbotResponse:
        results = await self._extract_ordered_items_and_match_to_menu(request)
        valid, incomplete, extra_messages = await self._validate_and_partition_confirmed(results)
        non_confirmed = [result for result in results if result.status != "confirmed"]
        response = self._builder.build_response(valid + incomplete + non_confirmed, request)
        if extra_messages:
            response.chatbot_message += "\n\n" + "\n\n".join(extra_messages)
        return response

    async def _extract_ordered_items_and_match_to_menu(
        self,
        request: BotInteractionRequest,
    ) -> list[_MatchResult]:
        items, menu_names = await asyncio.gather(
            self._extractor.extract_order_items(
                latest_message=request.latest_message,
                message_history=request.message_history,
            ),
            menu_repository.list_item_names(),
        )
        tasks = [
            self._matcher.match_item(
                item,
                menu_names,
                message_history=request.message_history,
                latest_message=request.latest_message,
            )
            for item in items
        ]
        return list(await asyncio.gather(*tasks))

    async def _validate_and_partition_confirmed(
        self,
        results: list[_MatchResult],
    ) -> tuple[list[_MatchResult], list[_MatchResult], list[str]]:
        valid = [result for result in results if result.status == "confirmed"]
        return valid, [], []

    async def _handle_add_to_order(self, request: BotInteractionRequest) -> ChatbotResponse:
        add_result, menu_names = await asyncio.gather(
            self._extractor.extract_add_items(
                latest_message=request.latest_message,
                order_state=request.order_state or {},
                message_history=request.message_history,
            ),
            menu_repository.list_item_names(),
        )

        order_state = dict(request.order_state or {"items": []})
        match_tasks = [
            self._matcher.match_item(
                item,
                menu_names,
                message_history=request.message_history,
                latest_message=request.latest_message,
            )
            for item in add_result.new_items
        ]
        match_results = list(await asyncio.gather(*match_tasks)) if match_tasks else []

        valid, incomplete, extra_messages = await self._validate_and_partition_confirmed(
            match_results
        )
        non_confirmed = [result for result in match_results if result.status != "confirmed"]
        response = self._builder.build_response(
            valid + incomplete + non_confirmed,
            request,
            existing_order_state=order_state,
        )
        if extra_messages:
            response.chatbot_message += "\n\n" + "\n\n".join(extra_messages)
        return response

    async def _handle_remove_from_order(self, request: BotInteractionRequest) -> ChatbotResponse:
        results = await self._resolve_and_match_remove(request)
        order_state = dict(request.order_state or {"items": []})
        messages: list[str] = []

        confirmed = [result for result in results if result.status == "confirmed"]
        if confirmed:
            items_to_remove = [
                {**result.item.model_dump(), "name": result.canonical_name}
                for result in confirmed
            ]
            updated_items, removed_summaries, not_in_order = remove_items(order_state, items_to_remove)
            order_state = {"items": updated_items}
            if removed_summaries:
                messages.append(f"Removed {', '.join(removed_summaries)}.")
            if not_in_order:
                names = ", ".join(f'"{name}"' for name in not_in_order)
                messages.append(f"{names} wasn't in your order.")

        for result in [result for result in results if result.status == "ambiguous"]:
            options = ", ".join(f'"{candidate}"' for candidate in result.candidates)
            messages.append(f'I found a few matches for "{result.item.name}" — did you mean {options}?')

        _append_not_found_menu_messages(messages, [r for r in results if r.status == "not_found"])
        chatbot_message = (
            " ".join(messages)
            if messages
            else "I didn't catch that — which item would you like to remove?"
        )
        return ChatbotResponse(chatbot_message=chatbot_message, order_state=order_state)

    async def _resolve_and_match_remove(self, request: BotInteractionRequest) -> list[_MatchResult]:
        items, menu_names = await asyncio.gather(
            self._extractor.resolve_remove_item(
                latest_message=request.latest_message,
                message_history=request.message_history,
            ),
            menu_repository.list_item_names(),
        )
        tasks = [
            self._matcher.match_item(
                item,
                menu_names,
                message_history=request.message_history,
                latest_message=request.latest_message,
            )
            for item in items
        ]
        return list(await asyncio.gather(*tasks))

    async def _handle_swap_item(self, request: BotInteractionRequest) -> ChatbotResponse:
        swap, menu_names = await asyncio.gather(
            self._extractor.extract_swap_items(
                latest_message=request.latest_message,
                message_history=request.message_history,
            ),
            menu_repository.list_item_names(),
        )

        remove_tasks = [
            self._matcher.match_item(
                item,
                menu_names,
                message_history=request.message_history,
                latest_message=request.latest_message,
            )
            for item in swap.remove
        ]
        add_tasks = [
            self._matcher.match_item(
                item,
                menu_names,
                message_history=request.message_history,
                latest_message=request.latest_message,
            )
            for item in swap.add
        ]
        remove_results = list(await asyncio.gather(*remove_tasks))
        add_results = list(await asyncio.gather(*add_tasks))

        messages: list[str] = []
        order_state = request.order_state or {"items": []}

        confirmed_removals = [result for result in remove_results if result.status == "confirmed"]
        if confirmed_removals:
            items_to_remove = [
                {**result.item.model_dump(), "name": result.canonical_name}
                for result in confirmed_removals
            ]
            updated_items, removed_summaries, not_in_order = remove_items(order_state, items_to_remove)
            order_state = {"items": updated_items}
            if removed_summaries:
                messages.append(f"Removed {', '.join(removed_summaries)}.")
            if not_in_order:
                names = ", ".join(f'"{name}"' for name in not_in_order)
                messages.append(f"{names} wasn't in your order.")

        for result in [result for result in remove_results if result.status == "ambiguous"]:
            options = ", ".join(f'"{candidate}"' for candidate in result.candidates)
            messages.append(f'I found a few matches for "{result.item.name}" — did you mean {options}?')

        _append_not_found_menu_messages(
            messages,
            [result for result in remove_results if result.status == "not_found"],
        )

        valid_adds, incomplete_adds, add_extra_messages = await self._validate_and_partition_confirmed(
            add_results
        )
        confirmed_additions = valid_adds + incomplete_adds
        if confirmed_additions:
            new_items = [
                {
                    **result.item.model_dump(),
                    "name": result.canonical_name,
                    "item_id": menu_repository.get_item_id(result.canonical_name),
                }
                for result in confirmed_additions
            ]
            merged = merge_items(order_state, new_items)
            order_state = {"items": merged}
            names = ", ".join(
                f"{result.item.quantity}x {result.canonical_name}" for result in confirmed_additions
            )
            messages.append(f"Added {names} to your order.")
        if add_extra_messages:
            messages.extend(add_extra_messages)

        for result in [result for result in add_results if result.status == "ambiguous"]:
            options = ", ".join(f'"{candidate}"' for candidate in result.candidates)
            messages.append(f'I found a few matches for "{result.item.name}" — did you mean {options}?')

        _append_not_found_menu_messages(
            messages,
            [result for result in add_results if result.status == "not_found"],
        )

        chatbot_message = (
            " ".join(messages)
            if messages
            else "I didn't catch that — which items would you like to swap?"
        )
        return ChatbotResponse(chatbot_message=chatbot_message, order_state=order_state)

    async def _handle_cancel_order(self, request: BotInteractionRequest) -> ChatbotResponse:
        return ChatbotResponse(
            chatbot_message="Your order has been cancelled. What else can I get for you?",
            order_state={"items": []},
        )

    async def _handle_order_modifier_request(
        self,
        request: BotInteractionRequest,
    ) -> ChatbotResponse:
        from src.chatbot.features.visibility import ai_client as visibility_ai

        reply = await visibility_ai.handle_order_modifier_request(
            latest_message=request.latest_message,
            order_state=request.order_state or {},
            message_history=request.message_history,
        )
        return ChatbotResponse(chatbot_message=reply, order_state=request.order_state)
