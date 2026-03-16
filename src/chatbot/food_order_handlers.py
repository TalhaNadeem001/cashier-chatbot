from dataclasses import dataclass, field
from typing import Literal

from rapidfuzz import fuzz, process

from src.cache import cache_get
from src.chatbot.chatbot_ai import ChatbotAI
from src.chatbot.constants import FoodOrderState
from src.chatbot.exceptions import UnhandledStateError
from src.chatbot.schema import BotMessageRequest, BotMessageResponse, ModifyItem, OrderItem
from src.chatbot.state_resolver import FoodOrderStateResolver

MENU_ITEM_NAMES_KEY = "menu_item_names:{user_id}"


def _parse_safely_fo(value: str | None) -> FoodOrderState | None:
    if not value:
        return None
    try:
        return FoodOrderState(value.strip().lower())
    except ValueError:
        return None

# Thresholds
CONFIRMED_THRESHOLD = 70     # single top match at or above this → confirmed
NOT_FOUND_THRESHOLD = 50     # top match below this → item does not exist on menu
AMBIGUITY_GAP = 6            # top N matches within this score range of each other → ambiguous


@dataclass
class _MatchResult:
    item: OrderItem
    status: Literal["confirmed", "ambiguous", "not_found"]
    canonical_name: str | None = None
    candidates: list[str] = field(default_factory=list)


class FoodOrderHandlerFactory:
    def __init__(self, ai: ChatbotAI):
        self._ai = ai
        self._handlers = {
            FoodOrderState.NEW_ORDER: self._handle_new_order,
            FoodOrderState.ADD_TO_ORDER: self._handle_add_to_order,
            FoodOrderState.MODIFY_ORDER: self._handle_modify_order,
            FoodOrderState.REMOVE_FROM_ORDER: self._handle_remove_from_order,
            FoodOrderState.SWAP_ITEM: self._handle_swap_item,
            FoodOrderState.CANCEL_ORDER: self._handle_cancel_order,
        }

    async def handle(self, request: BotMessageRequest) -> BotMessageResponse:
        if not request.order_state:
            response = await self._handle_new_order(request)
            response.previous_food_order_state = FoodOrderState.NEW_ORDER.value
            return response

        previous_food_state = _parse_safely_fo(request.previous_food_order_state)
        resolver = FoodOrderStateResolver(ai=self._ai)
        food_order_state = await resolver.resolve(
            latest_message=request.latest_message,
            order_state=request.order_state,
            message_history=request.message_history,
            previous_food_order_state=previous_food_state,
        )
        handler = self._handlers.get(food_order_state)
        if handler is None:
            raise UnhandledStateError(f"No handler registered for food order state: '{food_order_state}'")
        response = await handler(request)
        response.previous_food_order_state = food_order_state.value
        return response

    async def _handle_new_order(self, request: BotMessageRequest) -> BotMessageResponse:
        results = await self._extract_and_match(request)

        has_confirmed = any(r.status == "confirmed" for r in results)
        if not has_confirmed and request.has_pending_clarification and request.message_history:
            menu_names = await self._fetch_menu_names(request.user_id)
            resolved = await self._ai.resolve_confirmation(
                latest_message=request.latest_message,
                message_history=request.message_history,
            )
            if resolved:
                results = [self._match_item(item, menu_names) for item in resolved]

        return self._build_response(results, request)

    async def _extract_and_match(self, request: BotMessageRequest) -> list[_MatchResult]:
        items = await self._ai.extract_order_items(
            latest_message=request.latest_message,
            message_history=request.message_history,
        )
        menu_names = await self._fetch_menu_names(request.user_id)
        return [self._match_item(item, menu_names) for item in items]

    # ── Fuzzy matching ────────────────────────────────────────────────────────

    async def _fetch_menu_names(self, user_id: str) -> list[str]:
        raw = await cache_get(MENU_ITEM_NAMES_KEY.format(user_id=user_id))
        if not raw:
            return []
        return [name.strip() for name in raw.split(",")]

    def _match_item(self, item: OrderItem, menu_names: list[str]) -> _MatchResult:
        if not menu_names:
            return _MatchResult(item=item, status="not_found")

        # Exact case-insensitive match always wins — skip fuzzy ambiguity checks
        for name in menu_names:
            if name.lower() == item.name.lower():
                return _MatchResult(item=item, status="confirmed", canonical_name=name)

        top_matches = process.extract(
            item.name,
            menu_names,
            scorer=fuzz.WRatio,
            limit=5,
        )  # [(name, score, index), ...]

        if not top_matches or top_matches[0][1] < NOT_FOUND_THRESHOLD:
            return _MatchResult(item=item, status="not_found")

        best_score = top_matches[0][1]

        if best_score >= CONFIRMED_THRESHOLD:
            # Check for a tie — multiple items within AMBIGUITY_GAP of the best score
            close_matches = [m for m in top_matches if best_score - m[1] <= AMBIGUITY_GAP]
            if len(close_matches) > 1:
                return _MatchResult(
                    item=item,
                    status="ambiguous",
                    candidates=[m[0] for m in close_matches],
                )
            return _MatchResult(
                item=item,
                status="confirmed",
                canonical_name=top_matches[0][0],
            )

        # Score is between NOT_FOUND and CONFIRMED thresholds → ambiguous
        close_matches = [m for m in top_matches if best_score - m[1] <= AMBIGUITY_GAP]
        return _MatchResult(
            item=item,
            status="ambiguous",
            candidates=[m[0] for m in close_matches],
        )

    # ── Response builder ──────────────────────────────────────────────────────

    def _build_response(
        self,
        results: list[_MatchResult],
        request: BotMessageRequest,
        existing_order_state: dict | None = None,
    ) -> BotMessageResponse:
        confirmed = [r for r in results if r.status == "confirmed"]
        ambiguous = [r for r in results if r.status == "ambiguous"]
        not_found = [r for r in results if r.status == "not_found"]

        messages: list[str] = []
        new_order_state: dict | None = None

        if confirmed:
            new_items = [{**r.item.model_dump(), "name": r.canonical_name} for r in confirmed]
            merged = self._merge_items(existing_order_state, new_items)
            new_order_state = {"items": merged}
            names = ", ".join(f'{r.item.quantity}x {r.canonical_name}' for r in confirmed)
            messages.append(f"Got it! I've added {names} to your order.")

        if not_found:
            names = ", ".join(f'"{r.item.name}"' for r in not_found)
            messages.append(f"Sorry, I couldn't find {names} on our menu. Could you double-check the name?")

        if ambiguous:
            for r in ambiguous:
                options = ", ".join(f'"{c}"' for c in r.candidates)
                messages.append(f'I found a few matches for "{r.item.name}" — did you mean {options}?')

        has_pending_clarification = bool(ambiguous or not_found)
        chatbot_message = " ".join(messages) if messages else "I didn't catch that — could you tell me what you'd like to order?"
        return BotMessageResponse(
            chatbot_message=chatbot_message,
            order_state=new_order_state or existing_order_state or request.order_state,
            has_pending_clarification=has_pending_clarification,
        )

    def _merge_items(self, existing_order_state: dict | None, new_items: list[dict]) -> list[dict]:
        existing_items: list[dict] = (existing_order_state or {}).get("items", [])

        def _key(item: dict) -> tuple:
            return (item["name"], item.get("modifier"))

        merged = {_key(item): dict(item) for item in existing_items}

        for new_item in new_items:
            key = _key(new_item)
            if key in merged:
                merged[key]["quantity"] += new_item["quantity"]
            else:
                merged[key] = new_item

        return list(merged.values())

    def _remove_items(
        self,
        order_state: dict,
        items_to_remove: list[dict],
    ) -> tuple[list[dict], list[str], list[str]]:
        """Returns (updated_items, removed_summaries, not_in_order_names)."""
        current = {item["name"]: dict(item) for item in order_state.get("items", [])}
        removed_summaries: list[str] = []
        not_in_order: list[str] = []

        for item in items_to_remove:
            name = item["name"]
            qty = item["quantity"]

            if name not in current:
                not_in_order.append(name)
                continue

            if qty >= current[name]["quantity"]:
                del current[name]
                removed_summaries.append(f"{name} (removed entirely)")
            else:
                current[name]["quantity"] -= qty
                removed_summaries.append(f"{name} (now {current[name]['quantity']}x)")

        return list(current.values()), removed_summaries, not_in_order

    def _build_remove_response(
        self,
        results: list[_MatchResult],
        request: BotMessageRequest,
    ) -> BotMessageResponse:
        confirmed = [r for r in results if r.status == "confirmed"]
        ambiguous = [r for r in results if r.status == "ambiguous"]
        not_found = [r for r in results if r.status == "not_found"]

        messages: list[str] = []
        new_order_state: dict | None = None

        if confirmed:
            items_to_remove = [{**r.item.model_dump(), "name": r.canonical_name} for r in confirmed]
            updated, removed_summaries, not_in_order = self._remove_items(
                request.order_state or {}, items_to_remove
            )
            new_order_state = {"items": updated}

            if removed_summaries:
                messages.append(f"Done! Removed {', '.join(removed_summaries)} from your order.")
            if not_in_order:
                names = ", ".join(f'"{n}"' for n in not_in_order)
                messages.append(f"{names} wasn't in your order.")

        if not_found:
            names = ", ".join(f'"{r.item.name}"' for r in not_found)
            messages.append(f"Sorry, I couldn't find {names} on our menu.")

        if ambiguous:
            for r in ambiguous:
                options = ", ".join(f'"{c}"' for c in r.candidates)
                messages.append(f'I found a few matches for "{r.item.name}" — did you mean {options}?')
        
        has_pending_clarification = bool(ambiguous or not_found)
        chatbot_message = " ".join(messages) if messages else "I didn't catch that — which item would you like to remove?"
        return BotMessageResponse(
            chatbot_message=chatbot_message,
            order_state=new_order_state or request.order_state,
            has_pending_clarification=has_pending_clarification,
        )

    async def _extract_and_match_add(self, request: BotMessageRequest) -> list[_MatchResult]:
        items = await self._ai.extract_add_items(
            latest_message=request.latest_message,
            order_state=request.order_state or {},
            message_history=request.message_history,
        )
        menu_names = await self._fetch_menu_names(request.user_id)
        return [self._match_item(item, menu_names) for item in items]

    async def _handle_add_to_order(self, request: BotMessageRequest) -> BotMessageResponse:
        results = await self._extract_and_match_add(request)
        return self._build_response(results, request, existing_order_state=request.order_state)

    def _apply_modification(
        self,
        order_state: dict,
        modification: ModifyItem,
        canonical_name: str,
    ) -> tuple[dict, str]:
        """Returns (updated_order_state, human_readable_summary)."""
        items = [dict(item) for item in order_state.get("items", [])]
        for item in items:
            if item["name"] == canonical_name:
                if modification.quantity is not None:
                    item["quantity"] = modification.quantity
                if modification.clear_modifier:
                    item["modifier"] = None
                elif modification.modifier is not None:
                    item["modifier"] = modification.modifier

                parts: list[str] = [f"now {item['quantity']}×"]
                if item.get("modifier"):
                    parts.append(f"({item['modifier']})")
                summary = f"Updated {canonical_name}: {' '.join(parts)}"
                return {"items": items}, summary

        return order_state, f'"{canonical_name}" not found in your order.'

    async def _handle_modify_order(self, request: BotMessageRequest) -> BotMessageResponse:
        modifications = await self._ai.extract_modify_items(
            latest_message=request.latest_message,
            order_state=request.order_state or {},
            message_history=request.message_history,
        )
        menu_names = await self._fetch_menu_names(request.user_id)

        # Match each ModifyItem name against the menu using existing _match_item
        match_results = [
            self._match_item(OrderItem(name=m.name, quantity=1), menu_names)
            for m in modifications
        ]

        messages: list[str] = []
        order_state = request.order_state or {"items": []}
        has_pending_clarification = False

        for modification, result in zip(modifications, match_results):
            if result.status == "confirmed":
                order_state, summary = self._apply_modification(
                    order_state, modification, result.canonical_name  # type: ignore[arg-type]
                )
                messages.append(summary)
            elif result.status == "not_found":
                messages.append(f'Sorry, I couldn\'t find "{modification.name}" on our menu.')
                has_pending_clarification = True
            else:
                options = ", ".join(f'"{c}"' for c in result.candidates)
                messages.append(f'I found a few matches for "{modification.name}" — did you mean {options}?')
                has_pending_clarification = True

        chatbot_message = " ".join(messages) if messages else "I didn't catch that — what would you like to modify?"
        return BotMessageResponse(
            chatbot_message=chatbot_message,
            order_state=order_state,
            has_pending_clarification=has_pending_clarification,
        )

    async def _resolve_and_match_remove(self, request: BotMessageRequest) -> list[_MatchResult]:
        items = await self._ai.resolve_remove_item(
            latest_message=request.latest_message,
            message_history=request.message_history,
        )
        menu_names = await self._fetch_menu_names(request.user_id)
        return [self._match_item(item, menu_names) for item in items]

    async def _handle_remove_from_order(self, request: BotMessageRequest) -> BotMessageResponse:
        results = await self._resolve_and_match_remove(request)
        return self._build_remove_response(results, request)

    async def _handle_swap_item(self, request: BotMessageRequest) -> BotMessageResponse:
        swap = await self._ai.extract_swap_items(
            latest_message=request.latest_message,
            message_history=request.message_history,
        )
        menu_names = await self._fetch_menu_names(request.user_id)

        remove_results = [self._match_item(item, menu_names) for item in swap.remove]
        add_results = [self._match_item(item, menu_names) for item in swap.add]

        messages: list[str] = []
        order_state = request.order_state or {"items": []}
        has_pending_clarification = False

        # Process removals
        confirmed_removals = [r for r in remove_results if r.status == "confirmed"]
        if confirmed_removals:
            items_to_remove = [{**r.item.model_dump(), "name": r.canonical_name} for r in confirmed_removals]
            updated_items, removed_summaries, not_in_order = self._remove_items(order_state, items_to_remove)
            order_state = {"items": updated_items}
            if removed_summaries:
                messages.append(f"Removed {', '.join(removed_summaries)}.")
            if not_in_order:
                names = ", ".join(f'"{n}"' for n in not_in_order)
                messages.append(f"{names} wasn't in your order.")

        for r in [r for r in remove_results if r.status == "ambiguous"]:
            options = ", ".join(f'"{c}"' for c in r.candidates)
            messages.append(f'I found a few matches for "{r.item.name}" — did you mean {options}?')
            has_pending_clarification = True

        for r in [r for r in remove_results if r.status == "not_found"]:
            messages.append(f'Sorry, I couldn\'t find "{r.item.name}" on our menu.')
            has_pending_clarification = True

        # Process additions
        confirmed_additions = [r for r in add_results if r.status == "confirmed"]
        if confirmed_additions:
            new_items = [{**r.item.model_dump(), "name": r.canonical_name} for r in confirmed_additions]
            merged = self._merge_items(order_state, new_items)
            order_state = {"items": merged}
            names = ", ".join(f"{r.item.quantity}x {r.canonical_name}" for r in confirmed_additions)
            messages.append(f"Added {names} to your order.")

        for r in [r for r in add_results if r.status == "ambiguous"]:
            options = ", ".join(f'"{c}"' for c in r.candidates)
            messages.append(f'I found a few matches for "{r.item.name}" — did you mean {options}?')
            has_pending_clarification = True

        for r in [r for r in add_results if r.status == "not_found"]:
            messages.append(f'Sorry, I couldn\'t find "{r.item.name}" on our menu.')
            has_pending_clarification = True

        chatbot_message = " ".join(messages) if messages else "I didn't catch that — which items would you like to swap?"
        return BotMessageResponse(
            chatbot_message=chatbot_message,
            order_state=order_state,
            has_pending_clarification=has_pending_clarification,
        )

    async def _handle_cancel_order(self, request: BotMessageRequest) -> BotMessageResponse:
        return BotMessageResponse(
            chatbot_message="Your order has been cancelled.",
            order_state={"items": []},
        )
