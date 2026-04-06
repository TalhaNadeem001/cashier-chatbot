import asyncio
from src.chatbot.clarification.builder import ClarificationBuilder, merge_items, remove_items
from src.chatbot.clarification.fuzzy_matcher import FuzzyMatcher, _MatchResult
from src.chatbot.constants import FoodOrderState, ModifierState
from src.chatbot.exceptions import UnhandledStateError
from src.chatbot.extraction.extractor import OrderExtractor
from src.chatbot.intent.resolver import FoodOrderStateResolver, ModifierStateResolver
from src.chatbot.schema import BotInteractionRequest, ChatbotResponse, ModifyItem, OrderItem
from src.menu.loader import (
    detect_combo,
    get_item_definition,
    get_item_price,
    get_menu_item_modifiers_and_add_ons,
    get_menu_item_names,
    validate_mod_selections,
)


def _format_disallowed_free_modifier_message(
    item_name: str,
    requested: str,
    allowed: list[str],
    *,
    ambiguous: bool,
    candidates: list[str],
) -> str:
    """User-facing copy when a free-text modifier doesn't fuzzy-match the menu."""
    lines = allowed
    bullets_allowed = "\n".join(f"• {opt}" for opt in lines)
    lead = (
        f'For this order, the modification "{requested}" isn\'t allowed for **{item_name}**.\n\n'
        f"Here's what you can choose instead:\n{bullets_allowed}"
    )
    if ambiguous and candidates:
        bullets_cands = "\n".join(f"• {c}" for c in candidates)
        lead = (
            f'For this order, the modification "{requested}" isn\'t allowed for **{item_name}**.\n\n'
            f"Did you mean one of these?\n{bullets_cands}\n\n"
            f"If not, here's everything we allow for this item:\n{bullets_allowed}"
        )
    return lead


def _assign_confirmed_modifier_to_selected_mods(
    canonical_modifier: str,
    item_name: str,
    current_selected_mods: dict,
) -> tuple[str | None, dict]:
    """Try to place a confirmed free-form modifier into the correct required mod group.

    Iterates required mod groups that have not yet been selected and checks whether
    the canonical modifier matches any option name (case-insensitive exact match).
    If matched: returns (None, updated_selected_mods) — clears the free-text field.
    If not matched: returns (canonical_modifier, current_selected_mods) unchanged.
    """
    item_def = get_item_definition(item_name)
    if not item_def:
        return canonical_modifier, current_selected_mods
    mods = item_def.get("mods", {}) or {}
    requires: list[str] = item_def.get("requires", []) or []
    for mod_key in requires:
        if mod_key in current_selected_mods:
            continue  # already selected — skip
        mod = mods.get(mod_key, {})
        for opt in mod.get("options", []):
            opt_name = opt.get("name", "") if isinstance(opt, dict) else str(opt)
            if opt_name.lower().strip() == canonical_modifier.lower().strip():
                return None, {**current_selected_mods, mod_key: opt_name}
    return canonical_modifier, current_selected_mods


class OrderStateHandler:
    def __init__(self):
        self._extractor = OrderExtractor()
        self._matcher = FuzzyMatcher()
        self._builder = ClarificationBuilder()
        self._handlers = {
            FoodOrderState.NEW_ORDER: self._handle_new_order,
            FoodOrderState.ADD_TO_ORDER: self._handle_add_to_order,
            FoodOrderState.MODIFY_ORDER: self._handle_modify_order,
            FoodOrderState.ADDING_MODIFIERS: self._handle_add_modifiers,
            FoodOrderState.REMOVE_FROM_ORDER: self._handle_remove_from_order,
            FoodOrderState.SWAP_ITEM: self._handle_swap_item,
            FoodOrderState.CANCEL_ORDER: self._handle_cancel_order,
            FoodOrderState.REVIEW_ORDER: self._handle_review_order,
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
            raise UnhandledStateError(f"No handler registered for food order state: '{food_order_state}'")
        response = await handler(request)
        response.previous_food_order_state = food_order_state.value
        return response

    async def _free_modifier_gate(
        self,
        r: _MatchResult,
        extra_msgs: list[str],
    ) -> None:
        """Normalize free-text ``modifier`` when it fuzzy-matches; if not, explain and keep the line as-is."""
        if r.status != "confirmed" or not r.canonical_name:
            return
        raw = r.item.modifier
        if raw is None or not str(raw).strip():
            return
        mods, add_ons = await get_menu_item_modifiers_and_add_ons(r.canonical_name)
        allowed = list(dict.fromkeys([*mods, *add_ons]))
        if not allowed:
            return
        fm = self._matcher.match_free_modifier(str(raw).strip(), allowed)
        if fm.status == "confirmed":
            canonical = fm.canonical if fm.canonical is not None else str(raw).strip()
            new_modifier, new_selected = _assign_confirmed_modifier_to_selected_mods(
                canonical, r.canonical_name, r.item.selected_mods or {}
            )
            r.item = r.item.model_copy(update={
                "modifier": new_modifier,
                "selected_mods": new_selected or None,
            })
            return
        if fm.status == "ambiguous":
            extra_msgs.append(
                _format_disallowed_free_modifier_message(
                    r.canonical_name,
                    str(raw).strip(),
                    allowed,
                    ambiguous=True,
                    candidates=fm.candidates,
                )
            )
            return
        extra_msgs.append(
            _format_disallowed_free_modifier_message(
                r.canonical_name,
                str(raw).strip(),
                allowed,
                ambiguous=False,
                candidates=[],
            )
        )

    async def _validate_and_partition_confirmed(
        self,
        results: list[_MatchResult],
    ) -> tuple[list[_MatchResult], list[_MatchResult], list[str]]:
        """Returns (valid_confirmed, incomplete_confirmed, extra_messages).

        valid_confirmed: confirmed items that passed mod validation
        incomplete_confirmed: confirmed items with missing required mods (still added to order)
        extra_messages: error/clarification messages for rejected items + missing mod prompts
        """
        valid: list[_MatchResult] = []
        incomplete: list[_MatchResult] = []
        extra_msgs: list[str] = []

        for r in results:
            if r.status != "confirmed":
                continue
            await self._free_modifier_gate(r, extra_msgs)
            errors, missing = validate_mod_selections(r.canonical_name, r.item.selected_mods or {})  # type: ignore[arg-type]
            if errors:
                error_lines = "\n".join(f"  - {e}" for e in errors)
                extra_msgs.append(
                    f'Sorry, some options for "{r.canonical_name}" are invalid:\n{error_lines}'
                )
            elif missing:
                incomplete.append(r)
                item_def = get_item_definition(r.canonical_name)  # type: ignore[arg-type]
                mods = (item_def or {}).get("mods", {})
                missing_lines = []
                for mk in missing:
                    mod = mods.get(mk, {})
                    label = mod.get("label", mk)
                    opts = ", ".join(
                        f"{o['name']}" + (f" (+${o['price']:.2f})" if o.get("price") else "")
                        for o in mod.get("options", [])
                        if isinstance(o, dict)
                    )
                    missing_lines.append(f"  - {label}: {opts}")
                extra_msgs.append(f"For {r.canonical_name}, still need:\n" + "\n".join(missing_lines))
            else:
                valid.append(r)
        return valid, incomplete, extra_msgs

    async def _handle_new_order(self, request: BotInteractionRequest) -> ChatbotResponse:
        results = await self._extract_ordered_items_and_match_to_menu(request)
        valid, incomplete, extra_msgs = await self._validate_and_partition_confirmed(results)
        non_confirmed = [r for r in results if r.status != "confirmed"]
        response = self._builder.build_response(valid + incomplete + non_confirmed, request)
        print("new order response", response)
        if extra_msgs:
            response.chatbot_message += "\n\n" + "\n\n".join(extra_msgs)
        return self._apply_combo_detection(response)

    async def _extract_ordered_items_and_match_to_menu(self, request: BotInteractionRequest) -> list[_MatchResult]:
        items, menu_names = await asyncio.gather(
            self._extractor.extract_order_items(
                latest_message=request.latest_message,
                message_history=request.message_history,
            ),
            get_menu_item_names(),
        )
        return [self._matcher.match_item(item, menu_names) for item in items]

    async def _extract_and_match_add(self, request: BotInteractionRequest) -> list[_MatchResult]:
        items, menu_names = await asyncio.gather(
            self._extractor.extract_add_items(
                latest_message=request.latest_message,
                order_state=request.order_state or {},
                message_history=request.message_history,
            ),
            get_menu_item_names(),
        )
        return [self._matcher.match_item(item, menu_names) for item in items]

    async def _handle_add_to_order(self, request: BotInteractionRequest) -> ChatbotResponse:
        results = await self._extract_and_match_add(request)
        valid, incomplete, extra_msgs = await self._validate_and_partition_confirmed(results)
        non_confirmed = [r for r in results if r.status != "confirmed"]
        response = self._builder.build_response(
            valid + incomplete + non_confirmed, request, existing_order_state=request.order_state
        )
        if extra_msgs:
            response.chatbot_message += "\n\n" + "\n\n".join(extra_msgs)
        return self._apply_combo_detection(response)

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
                if modification.clear_selected_mods:
                    item["selected_mods"] = None
                elif modification.selected_mods is not None:
                    existing = item.get("selected_mods") or {}
                    item["selected_mods"] = {**existing, **modification.selected_mods}

                parts: list[str] = [f"now {item['quantity']}×"]
                if item.get("modifier"):
                    parts.append(f"({item['modifier']})")
                summary = f"Updated {canonical_name}: {' '.join(parts)}"
                return {"items": items}, summary

        return order_state, f'"{canonical_name}" not found in your order.'

    async def _handle_add_modifiers(self, request: BotInteractionRequest) -> ChatbotResponse:
        return await self._handle_modify_order(request)

    async def _handle_modify_order(self, request: BotInteractionRequest) -> ChatbotResponse:
        modifications, menu_names = await asyncio.gather(
            self._extractor.extract_modify_items(
                latest_message=request.latest_message,
                order_state=request.order_state or {},
                message_history=request.message_history,
            ),
            get_menu_item_names(),
        )

        match_results = [
            self._matcher.match_item(OrderItem(name=m.name, quantity=1), menu_names)
            for m in modifications
        ]

        messages: list[str] = []
        order_state = request.order_state or {"items": []}

        for modification, result in zip(modifications, match_results):
            if result.status == "confirmed":
                mod = modification
                if mod.modifier is not None and str(mod.modifier).strip():
                    mlist, alist = await get_menu_item_modifiers_and_add_ons(result.canonical_name)  # type: ignore[arg-type]
                    allowed = list(dict.fromkeys([*mlist, *alist]))
                    if allowed:
                        fm = self._matcher.match_free_modifier(str(mod.modifier).strip(), allowed)
                        if fm.status == "confirmed":
                            canonical = fm.canonical if fm.canonical is not None else str(mod.modifier).strip()
                            new_modifier, new_selected = _assign_confirmed_modifier_to_selected_mods(
                                canonical, result.canonical_name, mod.selected_mods or {}  # type: ignore[arg-type]
                            )
                            mod = mod.model_copy(update={
                                "modifier": new_modifier,
                                "selected_mods": new_selected or None,
                            })
                        elif fm.status == "ambiguous":
                            messages.append(
                                _format_disallowed_free_modifier_message(
                                    result.canonical_name,  # type: ignore[arg-type]
                                    str(mod.modifier).strip(),
                                    allowed,
                                    ambiguous=True,
                                    candidates=fm.candidates,
                                )
                            )
                        elif fm.status == "not_found":
                            messages.append(
                                _format_disallowed_free_modifier_message(
                                    result.canonical_name,  # type: ignore[arg-type]
                                    str(mod.modifier).strip(),
                                    allowed,
                                    ambiguous=False,
                                    candidates=[],
                                )
                            )
                errors, _ = validate_mod_selections(
                    result.canonical_name, mod.selected_mods or {}  # type: ignore[arg-type]
                )
                if errors:
                    error_lines = "\n".join(f"  - {e}" for e in errors)
                    messages.append(
                        f'Sorry, some options for "{result.canonical_name}" are invalid:\n{error_lines}'
                    )
                    continue
                order_state, summary = self._apply_modification(
                    order_state, mod, result.canonical_name  # type: ignore[arg-type]
                )
                messages.append(summary)
                updated_item = next(
                    (i for i in order_state.get("items", []) if i["name"] == result.canonical_name),
                    None,
                )
                if updated_item:
                    _, still_missing = validate_mod_selections(
                        result.canonical_name,  # type: ignore[arg-type]
                        updated_item.get("selected_mods") or {},
                    )
                    if still_missing:
                        item_def_msg = get_item_definition(result.canonical_name)  # type: ignore[arg-type]
                        mods_msg = (item_def_msg or {}).get("mods", {})
                        missing_lines = []
                        for mk in still_missing:
                            mod_info = mods_msg.get(mk, {})
                            label = mod_info.get("label", mk)
                            opts = ", ".join(
                                f"{o['name']}" + (f" (+${o['price']:.2f})" if o.get("price") else "")
                                for o in mod_info.get("options", [])
                                if isinstance(o, dict)
                            )
                            missing_lines.append(f"  - {label}: {opts}")
                        messages.append(
                            f"For {result.canonical_name}, still need:\n" + "\n".join(missing_lines)
                        )
            elif result.status == "not_found":
                messages.append(f'Sorry, I couldn\'t find "{modification.name}" on our menu.')
            else:
                options = ", ".join(f'"{c}"' for c in result.candidates)
                messages.append(f'I found a few matches for "{modification.name}" — did you mean {options}?')

        chatbot_message = " ".join(messages) if messages else "I didn't catch that — what would you like to modify?"
        return self._apply_combo_detection(ChatbotResponse(
            chatbot_message=chatbot_message,
            order_state=order_state,
        ))

    async def _resolve_and_match_remove(self, request: BotInteractionRequest) -> list[_MatchResult]:
        items, menu_names = await asyncio.gather(
            self._extractor.resolve_remove_item(
                latest_message=request.latest_message,
                message_history=request.message_history,
            ),
            get_menu_item_names(),
        )
        return [self._matcher.match_item(item, menu_names) for item in items]

    async def _handle_remove_from_order(self, request: BotInteractionRequest) -> ChatbotResponse:
        results = await self._resolve_and_match_remove(request)
        return self._apply_combo_detection(self._builder.build_remove_response(results, request))

    async def _handle_swap_item(self, request: BotInteractionRequest) -> ChatbotResponse:
        swap, menu_names = await asyncio.gather(
            self._extractor.extract_swap_items(
                latest_message=request.latest_message,
                message_history=request.message_history,
            ),
            get_menu_item_names(),
        )

        remove_results = [self._matcher.match_item(item, menu_names) for item in swap.remove]
        add_results = [self._matcher.match_item(item, menu_names) for item in swap.add]

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

        for r in [r for r in remove_results if r.status == "not_found"]:
            messages.append(f'Sorry, I couldn\'t find "{r.item.name}" on our menu.')

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

        for r in [r for r in add_results if r.status == "not_found"]:
            messages.append(f'Sorry, I couldn\'t find "{r.item.name}" on our menu.')

        chatbot_message = " ".join(messages) if messages else "I didn't catch that — which items would you like to swap?"
        return self._apply_combo_detection(ChatbotResponse(
            chatbot_message=chatbot_message,
            order_state=order_state,
        ))

    def _apply_combo_detection(self, response: ChatbotResponse) -> ChatbotResponse:
        order_state = response.order_state
        if not order_state:
            return response
        items = order_state.get("items", [])
        combo = detect_combo(items)
        if combo:
            order_state = {**order_state, "combo": combo}
            ack = f"\n\nBy the way, that's our **{combo['original_name']}** combo deal at ${combo['price']:.2f}!"
            response = response.model_copy(update={
                "order_state": order_state,
                "chatbot_message": response.chatbot_message + ack,
            })
        else:
            if "combo" in order_state:
                order_state = {k: v for k, v in order_state.items() if k != "combo"}
                response = response.model_copy(update={"order_state": order_state})
        print("response", response)
        return response

    async def _handle_cancel_order(self, request: BotInteractionRequest) -> ChatbotResponse:
        return ChatbotResponse(
            chatbot_message="Your order has been cancelled. What else can I get for you?",
            order_state={"items": []},
        )

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
    def __init__(self) -> None:
        self._order_handler = OrderStateHandler()
        self._handlers = {
            ModifierState.NEW_MODIFIER: self._handle_new_modifier,
            ModifierState.MODIFY_MODIFIER: self._handle_modify_modifier,
            ModifierState.REMOVE_MODIFIER: self._handle_remove_modifier,
            ModifierState.COMPLETE_MODIFIER: self._handle_complete_modifier,
            ModifierState.NO_MODIFIER: self._handle_no_modifier,
        }

    async def handle(self, request: BotInteractionRequest) -> ChatbotResponse:
        resolver = ModifierStateResolver()
        modifier_state = await resolver.resolve(
            latest_message=request.latest_message,
            order_state=request.order_state or {},
            message_history=request.message_history,
        )

        handler = self._handlers.get(modifier_state)
        if handler is None:
            raise UnhandledStateError(f"No handler registered for modifier state: '{modifier_state}'")
        return await handler(request)

    def _format_order_summary(self, order_state: dict | None) -> str:
        items = (order_state or {}).get("items", [])
        if not items:
            return "Your order is empty. What would you like?"
        lines = []
        for item in items:
            qty = item.get("quantity", 1)
            name = item.get("name", "Unknown")
            modifier = item.get("modifier")
            selected = item.get("selected_mods") or {}
            parts = [f"{qty}x {name}"]
            if modifier:
                parts.append(f"[{modifier}]")
            if selected:
                parts.append(f"({', '.join(selected.values())})")
            lines.append("- " + " ".join(parts))
        return "Here's your order so far:\n" + "\n".join(lines) + "\n\nIs that all?"

    async def _handle_new_modifier(self, request: BotInteractionRequest) -> ChatbotResponse:
        return await self._order_handler._handle_modify_order(request)

    async def _handle_modify_modifier(self, request: BotInteractionRequest) -> ChatbotResponse:
        return await self._order_handler._handle_modify_order(request)

    async def _handle_remove_modifier(self, request: BotInteractionRequest) -> ChatbotResponse:
        return await self._order_handler._handle_modify_order(request)

    async def _handle_complete_modifier(self, request: BotInteractionRequest) -> ChatbotResponse:
        return ChatbotResponse(
            chatbot_message=self._format_order_summary(request.order_state),
            order_state=request.order_state,
        )

    async def _handle_no_modifier(self, request: BotInteractionRequest) -> ChatbotResponse:
        return ChatbotResponse(
            chatbot_message=self._format_order_summary(request.order_state),
            order_state=request.order_state,
        )
