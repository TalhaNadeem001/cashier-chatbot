import asyncio

from src.chatbot.cart.ai_client import polish_food_order_reply
from src.chatbot.cart.combo_service import apply_best_combo
from src.chatbot.cart.item_detection_service import validate_order_item_modifiers, validate_order_items
from src.chatbot.cart.utils import (
    build_order_update_message,
    normalize_order_items,
    strip_order_state_for_delta,
)
from src.chatbot.clarification.fuzzy_matcher import FuzzyMatcher, _MatchResult
from src.chatbot.exceptions import AIServiceError
from src.chatbot.extraction.extractor import OrderExtractor
from src.chatbot.internal_schemas import MenuMatchIssue, ModifierValidationIssue, OrderProcessingOutcome
from src.chatbot.schema import BotInteractionRequest, ChatbotResponse, OrderItem
from src.menu.loader import (
    get_item_id,
    get_menu_item_names,
    get_order_item_line_total,
    get_order_item_unit_price,
    resolve_mod_ids_from_string,
)


async def _enrich_items_with_resolved_mods(items: list[dict]) -> list[dict]:
    enriched = []
    for item in items:
        enriched_item = dict(item)
        if not enriched_item.get("item_id"):
            enriched_item["item_id"] = get_item_id(enriched_item.get("name", ""))
        modifier_str = enriched_item.get("modifier") or ""
        if modifier_str.strip():
            enriched_item["resolved_mods"] = resolve_mod_ids_from_string(
                enriched_item.get("name", ""),
                modifier_str,
            )
        enriched.append(enriched_item)
    return enriched


async def _enrich_order_state_with_prices(order_state: dict) -> dict:
    items = order_state.get("items", [])
    enriched = []
    order_total = 0
    for item in items:
        enriched_item = dict(item)
        unit_price = get_order_item_unit_price(enriched_item)
        line_total = get_order_item_line_total(enriched_item)
        if unit_price is not None:
            enriched_item["unit_price"] = unit_price
        else:
            enriched_item.pop("unit_price", None)
        if line_total is not None:
            enriched_item["item_total"] = line_total
            order_total += line_total
        else:
            enriched_item.pop("item_total", None)
        enriched.append(enriched_item)
    return {**order_state, "items": enriched, "order_total": order_total}


def _build_canonical_items(results: list[_MatchResult]) -> list[dict]:
    confirmed_items = [
        {
            "name": result.canonical_name,
            "quantity": result.item.quantity,
            "modifier": result.item.modifier,
            "item_id": get_item_id(result.canonical_name or ""),
        }
        for result in results
        if result.status == "confirmed" and result.canonical_name
    ]
    return normalize_order_items(confirmed_items)


def _build_menu_match_issues(results: list[_MatchResult]) -> list[MenuMatchIssue]:
    issues: list[MenuMatchIssue] = []
    for result in results:
        if result.status == "confirmed":
            continue
        issues.append(
            MenuMatchIssue(
                kind=result.status,
                requested_name=result.item.name,
                candidates=result.candidates,
                clarification_message=result.clarification_message,
            )
        )
    return issues


def _last_assistant_message(message_history: list | None) -> str:
    if not message_history:
        return ""
    for message in reversed(message_history):
        if getattr(message, "role", None) == "assistant":
            return getattr(message, "content", "")
        if isinstance(message, dict) and message.get("role") == "assistant":
            return str(message.get("content", ""))
    return ""


def _dedupe_invalid_modifiers(issues: list[ModifierValidationIssue]) -> list[ModifierValidationIssue]:
    deduped: list[ModifierValidationIssue] = []
    seen: set[tuple[str, str]] = set()

    for issue in issues:
        key = (issue.item_name.strip().lower(), issue.invalid_modifier.strip().lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(issue)

    return deduped


def _dedupe_option_names(options: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for option in options:
        text = str(option).strip()
        normalized = text.lower()
        if not text or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(text)
    return deduped


def _group_invalid_modifier_issues(issues: list[ModifierValidationIssue]) -> list[dict]:
    groups: list[dict] = []
    index_by_key: dict[tuple[str, tuple[str, ...]], int] = {}

    for issue in issues:
        item_name = issue.item_name.strip()
        allowed_options = _dedupe_option_names(issue.allowed_options)
        key = (
            item_name.lower(),
            tuple(option.lower() for option in allowed_options),
        )
        if key not in index_by_key:
            index_by_key[key] = len(groups)
            groups.append(
                {
                    "item_name": item_name,
                    "allowed_options": allowed_options,
                    "invalid_modifiers": [],
                }
            )

        invalid_modifier = issue.invalid_modifier.strip()
        group = groups[index_by_key[key]]
        if invalid_modifier and invalid_modifier.lower() not in {
            value.lower() for value in group["invalid_modifiers"]
        }:
            group["invalid_modifiers"].append(invalid_modifier)

    return groups


def _format_modifier_list(values: list[str]) -> str:
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} and {values[1]}"
    return f"{', '.join(values[:-1])}, and {values[-1]}"


def _build_invalid_modifier_messages(issues: list[ModifierValidationIssue]) -> list[str]:
    messages: list[str] = []
    for group in _group_invalid_modifier_issues(issues):
        invalid_modifiers = group["invalid_modifiers"]
        allowed = ", ".join(group["allowed_options"])
        if len(invalid_modifiers) == 1:
            messages.append(
                f'{invalid_modifiers[0]} is not allowed for {group["item_name"]}. Allowed options are {allowed}.'
            )
            continue
        messages.append(
            f'{_format_modifier_list(invalid_modifiers)} are not allowed for {group["item_name"]}. Allowed options are {allowed}.'
        )
    return messages


def _build_fallback_cashier_reply(order_state: dict, outcome: OrderProcessingOutcome) -> str:
    current_order = strip_order_state_for_delta(order_state)
    items = current_order.get("items", [])
    if not items:
        return "Your order is now empty. What would you like to order?"

    reply_parts = [build_order_update_message(items)]

    if outcome.combo_event is not None and outcome.combo_event.kind == "attached":
        combo_price = outcome.combo_event.combo_price or 0
        reply_parts.append(
            f"That also matches our {outcome.combo_event.combo_name} combo at ${combo_price / 100:.2f}."
        )
    elif outcome.combo_event is not None and outcome.combo_event.kind == "removed":
        reply_parts.append(f"The {outcome.combo_event.combo_name} combo no longer applies.")

    reply_parts.extend(_build_invalid_modifier_messages(outcome.invalid_modifiers))

    for issue in outcome.menu_match_issues:
        if issue.kind == "ambiguous":
            if issue.clarification_message:
                reply_parts.append(issue.clarification_message)
            else:
                options = ", ".join(issue.candidates)
                reply_parts.append(f'I found a few matches for "{issue.requested_name}" — did you mean {options}?')
        elif issue.clarification_message:
            reply_parts.append(issue.clarification_message)
        else:
            reply_parts.append(f'Sorry, I could not find "{issue.requested_name}" on our menu.')

    for requirement in outcome.follow_up_requirements:
        if requirement.kind == "burger_patties":
            options = ", ".join(
                f"{option['label']} (${option['price']:.2f})"
                for option in requirement.details.get("options", [])
            )
            reply_parts.append(f"For your {requirement.item_name}, how many patties would you like: {options}?")
        elif requirement.kind == "wings_quantity":
            reply_parts.append(
                f"Please specify the quantity for {requirement.item_name}. Allowed quantities are 6, 12, 18, 24, or 30."
            )
        elif requirement.kind == "wings_flavor":
            flavors = ", ".join(requirement.details.get("available_flavors", []))
            reply_parts.append(f"What flavor would you like for your {requirement.item_name}? Available flavors are {flavors}.")
        elif requirement.kind == "wings_flavor_limit":
            reply_parts.append(
                f"You can only choose {requirement.details.get('max_flavors')} flavor(s) for your {requirement.details.get('quantity')} piece {requirement.item_name}. Please reduce your selection."
            )

    return " ".join(reply_parts)


class OrderStateHandler:
    def __init__(self):
        self._extractor = OrderExtractor()
        self._matcher = FuzzyMatcher()

    async def handle(self, request: BotInteractionRequest) -> ChatbotResponse:
        print("[order] start.handle latest_message:", request.latest_message)
        print("[order] incoming_order_state:", request.order_state)

        previous_order = strip_order_state_for_delta(request.order_state)
        print("[order] stripped_order_state:", previous_order)

        confirmation_items = await self._try_resolve_confirmation_items(request)
        confirmation_resolved = confirmation_items is not None

        if confirmation_items is not None:
            print("[order] confirmation_reply_branch_taken")
            proposed_items = confirmation_items
        else:
            delta_result = await self._extractor.apply_order_delta(
                latest_message=request.latest_message,
                order_state=previous_order,
                message_history=request.message_history,
            )
            proposed_items = normalize_order_items(
                [item.model_dump(exclude_none=True) for item in delta_result.items]
            )
            print("[order] proposed_items_after_delta:", proposed_items)

        match_results = await self._match_items_to_menu(proposed_items, request)
        print("[order] match_results:", self._serialize_match_results(match_results))
        menu_match_issues = _build_menu_match_issues(match_results)
        print("[order] menu_match_issues:", [issue.model_dump() for issue in menu_match_issues])

        if confirmation_items is not None:
            if menu_match_issues:
                accepted_items = previous_order.get("items", [])
            else:
                accepted_items = normalize_order_items(
                    [
                        *previous_order.get("items", []),
                        *_build_canonical_items(match_results),
                    ]
                )
            print("[order] accepted_items_after_confirmation:", accepted_items)
        else:
            accepted_items = _build_canonical_items(match_results)
            print("[order] accepted_items_after_menu_match:", accepted_items)

        modifier_validation = await validate_order_item_modifiers(
            accepted_items,
            latest_message=request.latest_message,
        )
        accepted_items = normalize_order_items(modifier_validation.items)
        print("[order] accepted_items_after_modifier_validation:", accepted_items)
        print(
            "[order] invalid_modifiers_after_delta:",
            [issue.model_dump() for issue in modifier_validation.invalid_modifiers],
        )

        final_order_state, outcome = await self._finalize_order_state(
            request=request,
            accepted_items=accepted_items,
            previous_order=previous_order,
            menu_match_issues=menu_match_issues,
            confirmation_resolved=confirmation_resolved and not menu_match_issues,
            early_invalid_modifiers=modifier_validation.invalid_modifiers,
        )

        reply = await self._generate_final_reply(
            request=request,
            final_order_state=final_order_state,
            outcome=outcome,
        )

        print("[order] final_cashier_reply:", reply)
        print("[order] final_order_state:", final_order_state)
        return ChatbotResponse(
            chatbot_message=reply,
            order_state=final_order_state,
        )

    async def _try_resolve_confirmation_items(self, request: BotInteractionRequest) -> list[dict] | None:
        last_assistant_message = _last_assistant_message(request.message_history)
        print("[order] last_assistant_message:", last_assistant_message)
        if "did you mean" not in last_assistant_message.lower():
            return None

        print("[order] confirmation_reply_detected")
        confirmed_items = await self._extractor.resolve_confirmation(
            latest_message=request.latest_message,
            message_history=request.message_history,
        )
        serialized_items = [item.model_dump(exclude_none=True) for item in confirmed_items]
        print("[order] confirmed_items_from_history:", serialized_items)
        if not confirmed_items:
            return None

        return serialized_items

    async def _match_items_to_menu(
        self,
        items: list[dict],
        request: BotInteractionRequest,
    ) -> list[_MatchResult]:
        if not items:
            print("[order] no_items_to_match")
            return []

        order_items = [OrderItem(**item) for item in items]
        menu_names = await get_menu_item_names()
        print("[order] matching_item_count:", len(order_items))
        return list(
            await asyncio.gather(
                *[
                    self._matcher.match_item(
                        item,
                        menu_names,
                        message_history=request.message_history,
                        latest_message=request.latest_message,
                    )
                    for item in order_items
                ]
            )
        )

    async def _finalize_order_state(
        self,
        request: BotInteractionRequest,
        accepted_items: list[dict],
        previous_order: dict,
        menu_match_issues: list[MenuMatchIssue],
        confirmation_resolved: bool,
        early_invalid_modifiers: list[ModifierValidationIssue],
    ) -> tuple[dict, OrderProcessingOutcome]:
        print("[order] finalize.start_items:", accepted_items)

        combo_result = await apply_best_combo(
            {"items": accepted_items},
            previous_combo=(request.order_state or {}).get("combo"),
        )
        print("[order] finalize.after_combo:", combo_result.order_state)
        print(
            "[order] finalize.combo_event:",
            combo_result.combo_event.model_dump() if combo_result.combo_event else None,
        )

        validation_result = await validate_order_items(
            combo_result.order_state.get("items", []),
            latest_message=request.latest_message,
        )
        print("[order] finalize.validation_items:", validation_result.items)
        print(
            "[order] finalize.invalid_modifiers:",
            [issue.model_dump() for issue in validation_result.invalid_modifiers],
        )
        print(
            "[order] finalize.follow_up_requirements:",
            [requirement.model_dump() for requirement in validation_result.follow_up_requirements],
        )

        invalid_modifiers = _dedupe_invalid_modifiers(
            [*early_invalid_modifiers, *validation_result.invalid_modifiers]
        )
        print(
            "[order] finalize.combined_invalid_modifiers:",
            [issue.model_dump() for issue in invalid_modifiers],
        )

        working_order_state = {**combo_result.order_state, "items": validation_result.items}

        resolved_items = await _enrich_items_with_resolved_mods(working_order_state.get("items", []))
        print("[order] finalize.resolved_items:", resolved_items)
        working_order_state = {**working_order_state, "items": resolved_items}

        enriched_order_state = await _enrich_order_state_with_prices(working_order_state)
        print("[order] finalize.enriched_order_state:", enriched_order_state)

        outcome = OrderProcessingOutcome(
            previous_order=previous_order,
            accepted_order=strip_order_state_for_delta(enriched_order_state),
            menu_match_issues=menu_match_issues,
            invalid_modifiers=invalid_modifiers,
            follow_up_requirements=validation_result.follow_up_requirements,
            combo_event=combo_result.combo_event,
            confirmation_resolved=confirmation_resolved,
            order_empty=not bool(strip_order_state_for_delta(enriched_order_state).get("items")),
        )
        print("[order] finalize.processing_outcome:", outcome.model_dump())
        return enriched_order_state, outcome

    async def _generate_final_reply(
        self,
        request: BotInteractionRequest,
        final_order_state: dict,
        outcome: OrderProcessingOutcome,
    ) -> str:
        try:
            reply = await polish_food_order_reply(
                order_state=final_order_state,
                order_outcome=outcome.model_dump(mode="json"),
                latest_message=request.latest_message,
                message_history=request.message_history,
            )
        except AIServiceError as exc:
            print("[reply] fallback_due_to_error:", exc)
            return _build_fallback_cashier_reply(final_order_state, outcome)

        return reply

    def _serialize_match_results(self, results: list[_MatchResult]) -> list[dict]:
        return [
            {
                "item": result.item.model_dump(exclude_none=True),
                "status": result.status,
                "canonical_name": result.canonical_name,
                "candidates": result.candidates,
                "clarification_message": result.clarification_message,
            }
            for result in results
        ]
