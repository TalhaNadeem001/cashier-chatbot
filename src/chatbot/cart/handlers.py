# Standard library
import asyncio

# Chatbot clarification
from src.chatbot.clarification.builder import ClarificationBuilder, merge_items, remove_items
from src.chatbot.clarification.fuzzy_matcher import FuzzyMatcher, _MatchResult

# Chatbot intent
from src.chatbot.intent.ai_client import assign_item_modifiers, remove_item_modifiers, swap_item_modifiers
from src.chatbot.intent.resolver import FoodOrderStateResolver, ModifierOrderStateResolver

# Chatbot extraction & schema
from src.chatbot.extraction.extractor import OrderExtractor
from src.chatbot.schema import BotInteractionRequest, ChatbotResponse

# Chatbot constants & exceptions
from src.chatbot.constants import FoodOrderState, ModifierOrderState
from src.chatbot.exceptions import UnhandledStateError

# Chatbot cart services
from src.chatbot.cart.combo_service import detect_and_attach_combo
from src.chatbot.cart.item_detection_service import validate_order_items
from src.chatbot.cart.utils import extract_items, merge_modifier_items

# Menu utilities
from src.menu.loader import get_item_price, get_menu_item_names


def _append_not_found_menu_messages(messages: list[str], not_found: list[_MatchResult]) -> None:
    custom = list(dict.fromkeys(m for r in not_found if (m := r.clarification_message)))
    messages.extend(custom)
    generic = [r for r in not_found if not r.clarification_message]
    if generic:
        names = ", ".join(f'"{r.item.name}"' for r in generic)
        messages.append(f"Sorry, I couldn't find {names} on our menu.")


class OrderStateHandler:
    def __init__(self):
        self._extractor = OrderExtractor()
        self._matcher = FuzzyMatcher()
        self._builder = ClarificationBuilder()
        self._handlers = {
            FoodOrderState.NEW_ORDER: self._handle_new_order,
            FoodOrderState.ADD_TO_ORDER: self._handle_add_to_order,
            FoodOrderState.REMOVE_FROM_ORDER: self._handle_remove_from_order,
            FoodOrderState.SWAP_ITEM: self._handle_swap_item,
            FoodOrderState.CANCEL_ORDER: self._handle_cancel_order,
            FoodOrderState.REVIEW_ORDER: self._handle_review_order,
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
        print("food order state", food_order_state)

        handler = self._handlers.get(food_order_state)
        if handler is None:
            raise UnhandledStateError(f"No handler registered for food order state: '{food_order_state}'")
        response = await handler(request)
        response.previous_food_order_state = food_order_state.value
        return response

    async def _handle_new_order(self, request: BotInteractionRequest) -> ChatbotResponse:
        results = await self._extract_ordered_items_and_match_to_menu(request)
        valid, incomplete, extra_msgs = await self._validate_and_partition_confirmed(results)
        non_confirmed = [r for r in results if r.status != "confirmed"]
        response = self._builder.build_response(valid + incomplete + non_confirmed, request)
        if extra_msgs:
            response.chatbot_message += "\n\n" + "\n\n".join(extra_msgs)
        return response

    async def _extract_ordered_items_and_match_to_menu(self, request: BotInteractionRequest) -> list[_MatchResult]:
        items, menu_names = await asyncio.gather(
            self._extractor.extract_order_items(
                latest_message=request.latest_message,
                message_history=request.message_history,
            ),
            get_menu_item_names(),
        )
        return list(await asyncio.gather(*[self._matcher.match_item(item, menu_names, message_history=request.message_history, latest_message=request.latest_message) for item in items]))
    
    async def _validate_and_partition_confirmed(self, results: list[_MatchResult]) -> tuple[list[_MatchResult], list[_MatchResult], list[str]]:
        valid = [r for r in results if r.status == "confirmed"]
        return valid, [], []

    async def _handle_add_to_order(self, request: BotInteractionRequest) -> ChatbotResponse:
        add_result, menu_names = await asyncio.gather(
            self._extractor.extract_add_items(
                latest_message=request.latest_message,
                order_state=request.order_state or {},
                message_history=request.message_history,
            ),
            get_menu_item_names(),
        )

        order_state = dict(request.order_state or {"items": []})

        match_results = list(await asyncio.gather(
            *[self._matcher.match_item(item, menu_names, message_history=request.message_history, latest_message=request.latest_message) for item in add_result.new_items]
        )) if add_result.new_items else []

        valid, incomplete, extra_msgs = await self._validate_and_partition_confirmed(match_results)
        non_confirmed = [r for r in match_results if r.status != "confirmed"]

        response = self._builder.build_response(
            valid + incomplete + non_confirmed,
            request,
            existing_order_state=order_state,
        )

        if extra_msgs:
            response.chatbot_message += "\n\n" + "\n\n".join(extra_msgs)
        return response

    async def _handle_remove_from_order(self, request: BotInteractionRequest) -> ChatbotResponse:
        results = await self._resolve_and_match_remove(request)
        order_state = dict(request.order_state or {"items": []})
        messages: list[str] = []

        confirmed = [r for r in results if r.status == "confirmed"]
        if confirmed:
            items_to_remove = [{**r.item.model_dump(), "name": r.canonical_name} for r in confirmed]
            updated_items, removed_summaries, not_in_order = remove_items(order_state, items_to_remove)
            order_state = {"items": updated_items}
            if removed_summaries:
                messages.append(f"Removed {', '.join(removed_summaries)}.")
            if not_in_order:
                names = ", ".join(f'"{n}"' for n in not_in_order)
                messages.append(f"{names} wasn't in your order.")

        for r in [r for r in results if r.status == "ambiguous"]:
            options = ", ".join(f'"{c}"' for c in r.candidates)
            messages.append(f'I found a few matches for "{r.item.name}" — did you mean {options}?')
        

        _append_not_found_menu_messages(messages, [r for r in results if r.status == "not_found"])

        chatbot_message = " ".join(messages) if messages else "I didn't catch that — which item would you like to remove?"
        return ChatbotResponse(chatbot_message=chatbot_message, order_state=order_state)
    
    async def _resolve_and_match_remove(self, request: BotInteractionRequest) -> list[_MatchResult]:
        items, menu_names = await asyncio.gather(
            self._extractor.resolve_remove_item(
                latest_message=request.latest_message,
                message_history=request.message_history,
            ),
            get_menu_item_names(),
        )
        return list(await asyncio.gather(*[self._matcher.match_item(item, menu_names, message_history=request.message_history, latest_message=request.latest_message) for item in items]))

    async def _handle_swap_item(self, request: BotInteractionRequest) -> ChatbotResponse:
        swap, menu_names = await asyncio.gather(
            self._extractor.extract_swap_items(
                latest_message=request.latest_message,
                message_history=request.message_history,
            ),
            get_menu_item_names(),
        )

        remove_results = list(await asyncio.gather(*[self._matcher.match_item(item, menu_names, message_history=request.message_history, latest_message=request.latest_message) for item in swap.remove]))
        add_results = list(await asyncio.gather(*[self._matcher.match_item(item, menu_names, message_history=request.message_history, latest_message=request.latest_message) for item in swap.add]))

        messages: list[str] = []
        order_state = request.order_state or {"items": []}

        # Process removals
        confirmed_removals = [r for r in remove_results if r.status == "confirmed"]
        if confirmed_removals:
            items_to_remove = [{**r.item.model_dump(), "name": r.canonical_name} for r in confirmed_removals]
            updated_items, removed_summaries, not_in_order = remove_items(order_state, items_to_remove)
            order_state = {"items": updated_items}
            if removed_summaries:
                messages.append(f"Removed {', '.join(removed_summaries)}.")
            if not_in_order:
                names = ", ".join(f'"{n}"' for n in not_in_order)
                messages.append(f"{names} wasn't in your order.")

        for r in [r for r in remove_results if r.status == "ambiguous"]:
            options = ", ".join(f'"{c}"' for c in r.candidates)
            messages.append(f'I found a few matches for "{r.item.name}" — did you mean {options}?')

        _append_not_found_menu_messages(messages, [r for r in remove_results if r.status == "not_found"])

        # Process additions
        valid_adds, incomplete_adds, add_extra_msgs = await self._validate_and_partition_confirmed(add_results)
        confirmed_additions = valid_adds + incomplete_adds
        if confirmed_additions:
            new_items = [{**r.item.model_dump(), "name": r.canonical_name} for r in confirmed_additions]
            merged = merge_items(order_state, new_items)
            order_state = {"items": merged}
            names = ", ".join(f"{r.item.quantity}x {r.canonical_name}" for r in confirmed_additions)
            messages.append(f"Added {names} to your order.")
        if add_extra_msgs:
            messages.extend(add_extra_msgs)

        for r in [r for r in add_results if r.status == "ambiguous"]:
            options = ", ".join(f'"{c}"' for c in r.candidates)
            messages.append(f'I found a few matches for "{r.item.name}" — did you mean {options}?')

        _append_not_found_menu_messages(messages, [r for r in add_results if r.status == "not_found"])

        chatbot_message = " ".join(messages) if messages else "I didn't catch that — which items would you like to swap?"
        return ChatbotResponse(
            chatbot_message=chatbot_message,
            order_state=order_state,
        )

    async def _handle_cancel_order(self, request: BotInteractionRequest) -> ChatbotResponse:
        return ChatbotResponse(
            chatbot_message="Your order has been cancelled. What else can I get for you?",
            order_state={"items": []},
        )

    async def _handle_order_modifier_request(self, request: BotInteractionRequest) -> ChatbotResponse:
        from src.chatbot.visibility import ai_client as visibility_ai
        reply = await visibility_ai.handle_order_modifier_request(
            latest_message=request.latest_message,
            order_state=request.order_state or {},
            message_history=request.message_history,
        )
        return ChatbotResponse(chatbot_message=reply, order_state=request.order_state)

    async def _handle_review_order(self, request: BotInteractionRequest) -> ChatbotResponse:
        items = (request.order_state or {}).get("items", [])
        if not items:
            return ChatbotResponse(
                chatbot_message="Your order is empty. What would you like to order?",
                order_state=request.order_state,
            )

        lines: list[str] = []
        total = 0.0
        for item in items:
            name = item.get("name", "Unknown item")
            quantity = item.get("quantity", 1)
            modifier = item.get("modifier")
            price = await get_item_price(name)

            label = name
            if modifier:
                label += f" [{modifier}]"

            qty_prefix = f"{quantity}x " if quantity > 1 else ""
            if price is not None:
                line_total = price * quantity
                total += line_total
                price_str = f"(${price:.2f} each)" if quantity > 1 else f"(${price:.2f})"
                lines.append(f"- {qty_prefix}{label} {price_str} = ${line_total:.2f}")
            else:
                lines.append(f"- {qty_prefix}{label}")

        items_text = "\n".join(lines)
        total_line = f"\n\nRunning total: ${total:.2f}" if total > 0 else ""
        message = f"Here's what you have so far:\n{items_text}{total_line}"

        return ChatbotResponse(
            chatbot_message=message,
            order_state=request.order_state,
        )


class ModifierStateHandler:
    def __init__(self):
        self._extractor = OrderExtractor()
        self._builder = ClarificationBuilder()
        self._handlers = {
            ModifierOrderState.ADD_MODIFIER:    self._handle_add_modifier,
            ModifierOrderState.REMOVE_MODIFIER: self._handle_remove_modifier,
            ModifierOrderState.SWAP_MODIFIER:   self._handle_swap_modifier,
            ModifierOrderState.CANCEL_MODIFIER: self._handle_cancel_modifier,
            ModifierOrderState.NO_MODIFIER:     self._handle_no_modifier,
        }

    async def handle(self, request: BotInteractionRequest, food_response: ChatbotResponse | None = None) -> ChatbotResponse:
        resolver = ModifierOrderStateResolver()
        modifier_state = await resolver.resolve(
            latest_message=request.latest_message,
            order_state=request.order_state or {},
            message_history=request.message_history,
        )
        print("modifier order state", modifier_state)

        handler = self._handlers.get(modifier_state)
        if handler is None:
            raise UnhandledStateError(f"No handler registered for modifier order state: '{modifier_state}'")
        return await handler(request, food_response)

    async def _handle_new_modifier(self, request: BotInteractionRequest, food_response: ChatbotResponse | None = None) -> ChatbotResponse:
        items = await extract_items(request)
        result = await assign_item_modifiers(
            latest_message=request.latest_message,
            items=items,
            message_history=request.message_history,
        )
        merged = await merge_modifier_items(result.items)
        base = (food_response or ChatbotResponse(chatbot_message="")).model_copy(
            update={"order_state": {"items": merged}}
        )
        return await self._detect_special_cases(base)

    async def _handle_add_modifier(self, request: BotInteractionRequest, food_response: ChatbotResponse | None = None) -> ChatbotResponse:
        items = await extract_items(request)
        result = await assign_item_modifiers(
            latest_message=request.latest_message,
            items=items,
            message_history=request.message_history,
        )
        merged = await merge_modifier_items(result.items)
        base = (food_response or ChatbotResponse(chatbot_message="")).model_copy(
            update={"order_state": {"items": merged}}
        )
        return await self._detect_special_cases(base)

    async def _handle_remove_modifier(self, request: BotInteractionRequest, food_response: ChatbotResponse | None = None) -> ChatbotResponse:
        items = await extract_items(request)
        result = await remove_item_modifiers(
            latest_message=request.latest_message,
            items=items,
            message_history=request.message_history,
        )
        merged = await merge_modifier_items(result.items)
        base = (food_response or ChatbotResponse(chatbot_message="")).model_copy(
            update={"order_state": {"items": merged}}
        )
        return await self._detect_special_cases(base)

    async def _handle_swap_modifier(self, request: BotInteractionRequest, food_response: ChatbotResponse | None = None) -> ChatbotResponse:
        items = await extract_items(request)
        result = await swap_item_modifiers(
            latest_message=request.latest_message,
            items=items,
            message_history=request.message_history,
        )
        merged = await merge_modifier_items(result.items)
        base = (food_response or ChatbotResponse(chatbot_message="")).model_copy(
            update={"order_state": {"items": merged}}
        )
        return await self._detect_special_cases(base)

    async def _handle_cancel_modifier(self, request: BotInteractionRequest, food_response: ChatbotResponse | None = None) -> ChatbotResponse:
        order_state = dict(request.order_state or {})
        items = [dict(item) for item in order_state.get("items", [])]
        for item in items:
            item["modifier"] = ""
        order_state["items"] = items
        base = (food_response or ChatbotResponse(chatbot_message="")).model_copy(
            update={"order_state": order_state}
        )
        return await self._detect_special_cases(base)

    async def _handle_no_modifier(self, request: BotInteractionRequest, food_response: ChatbotResponse | None = None) -> ChatbotResponse:
        base = (food_response or ChatbotResponse(chatbot_message="")).model_copy(
            update={"order_state": request.order_state}
        )
        return await self._detect_special_cases(base)
    
    async def _detect_special_cases(self, response: ChatbotResponse) -> ChatbotResponse:
        order_state = response.order_state
        if not order_state:
            return response
        items = order_state.get("items", [])
        response = await detect_and_attach_combo(items, response)
        response = await validate_order_items(items, response)
        return response