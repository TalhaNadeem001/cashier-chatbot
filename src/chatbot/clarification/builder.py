from src.chatbot.clarification.fuzzy_matcher import _MatchResult
from src.chatbot.schema import BotInteractionRequest, ChatbotResponse


def merge_items(existing_order_state: dict | None, new_items: list[dict]) -> list[dict]:
    existing_items: list[dict] = (existing_order_state or {}).get("items", [])

    def _key(item: dict) -> tuple:
        mods = item.get("selected_mods")
        if isinstance(mods, dict):
            mods_key = tuple(sorted(
                (k, tuple(v) if isinstance(v, list) else v)
                for k, v in mods.items()
            ))
        else:
            mods_key = None
        return (item["name"], item.get("modifier"), mods_key)

    merged = {_key(item): dict(item) for item in existing_items}

    for new_item in new_items:
        key = _key(new_item)
        if key in merged:
            merged[key]["quantity"] += new_item["quantity"]
        else:
            merged[key] = new_item

    return list(merged.values())


def remove_items(
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


class ClarificationBuilder:
    def build_response(
        self,
        results: list[_MatchResult],
        request: BotInteractionRequest,
        existing_order_state: dict | None = None,
    ) -> ChatbotResponse:
        confirmed = [r for r in results if r.status == "confirmed"]
        ambiguous = [r for r in results if r.status == "ambiguous"]
        not_found = [r for r in results if r.status == "not_found"]

        messages: list[str] = []
        new_order_state: dict | None = None

        if confirmed:
            new_items = [{**r.item.model_dump(), "name": r.canonical_name} for r in confirmed]
            merged = merge_items(existing_order_state, new_items)
            new_order_state = {"items": merged}

            def _item_label(r: _MatchResult) -> str:
                label = f"{r.item.quantity}x {r.canonical_name}"
                if r.item.selected_mods:
                    mod_vals = [str(v) for v in r.item.selected_mods.values() if v]
                    if mod_vals:
                        label += f" ({', '.join(mod_vals)})"
                return label

            names = ", ".join(_item_label(r) for r in confirmed)
            messages.append(f"Got it! I've added {names} to your order.")

        if not_found:
            names = ", ".join(f'"{r.item.name}"' for r in not_found)
            messages.append(f"Sorry, I couldn't find {names} on our menu. Could you double-check the name?")

        if ambiguous:
            for r in ambiguous:
                options = ", ".join(f'"{c}"' for c in r.candidates)
                messages.append(f'I found a few matches for "{r.item.name}" — did you mean {options}?')

        chatbot_message = " ".join(messages) if messages else "I didn't catch that — could you tell me what you'd like to order?"
        return ChatbotResponse(
            chatbot_message=chatbot_message,
            order_state=new_order_state or existing_order_state or request.order_state,
        )

    def build_remove_response(
        self,
        results: list[_MatchResult],
        request: BotInteractionRequest,
    ) -> ChatbotResponse:
        confirmed = [r for r in results if r.status == "confirmed"]
        ambiguous = [r for r in results if r.status == "ambiguous"]
        not_found = [r for r in results if r.status == "not_found"]

        messages: list[str] = []
        new_order_state: dict | None = None

        if confirmed:
            items_to_remove = [{**r.item.model_dump(), "name": r.canonical_name} for r in confirmed]
            updated, removed_summaries, not_in_order = remove_items(
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

        chatbot_message = " ".join(messages) if messages else "I didn't catch that — which item would you like to remove?"
        return ChatbotResponse(
            chatbot_message=chatbot_message,
            order_state=new_order_state or request.order_state,
        )
