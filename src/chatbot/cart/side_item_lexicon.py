import re
from dataclasses import dataclass
from typing import Literal

from src.chatbot.internal_schemas import AssumptionApplied, ClarificationIssue

LexiconKind = Literal["sauce", "dressing", "cheese", "protein_add_on", "side", "drink_generic"]


@dataclass(frozen=True)
class LexiconEntry:
    canonical_name: str
    kind: LexiconKind
    aliases: tuple[str, ...]
    can_be_modifier: bool = True
    can_be_side_item: bool = True


_LEXICON: tuple[LexiconEntry, ...] = (
    LexiconEntry("Ranch", "sauce", ("ranch", "ranch sauce")),
    LexiconEntry("Buffalo", "sauce", ("buffalo", "buffalo sauce")),
    LexiconEntry("Blue Cheese", "dressing", ("blue cheese", "bleu cheese")),
    LexiconEntry("Garlic Parm", "sauce", ("garlic parm", "garlic parmesan", "parm garlic")),
    LexiconEntry("Honey Mustard", "sauce", ("honey mustard",)),
    LexiconEntry("BBQ", "sauce", ("bbq", "barbecue", "barbeque")),
    LexiconEntry("Ketchup", "sauce", ("ketchup", "catsup")),
    LexiconEntry("Extra Cheese", "cheese", ("extra cheese", "cheese"), can_be_side_item=False),
    LexiconEntry("Extra Bacon", "protein_add_on", ("extra bacon", "bacon"), can_be_side_item=True),
    LexiconEntry("Extra Chicken", "protein_add_on", ("extra chicken", "add chicken"), can_be_side_item=False),
    LexiconEntry("Cole Slaw", "side", ("slaw", "cole slaw", "coleslaw"), can_be_modifier=False),
    LexiconEntry("Mac & Cheese", "side", ("mac and cheese", "mac n cheese", "mac & cheese"), can_be_modifier=False),
    LexiconEntry("Regular Fries", "side", ("fries", "french fries", "regular fries"), can_be_modifier=False),
    LexiconEntry("Soda", "drink_generic", ("soda", "drink", "pop"), can_be_modifier=False, can_be_side_item=True),
)

_SIDE_HINTS = (
    "on the side",
    "on side",
    "side",
    "side cup",
    "sauce cup",
    "extra order",
    "as a side",
)

_BOUND_HINTS = (
    "with ",
    " on ",
    "inside ",
    "without ",
    "no ",
)


def split_modifier_tokens(modifier_text: str | None) -> list[str]:
    if not modifier_text:
        return []
    return [token.strip() for token in str(modifier_text).split(",") if token.strip()]


def route_items_with_side_entities(
    items: list[dict],
    latest_message: str,
) -> tuple[list[dict], list[ClarificationIssue], list[AssumptionApplied]]:
    normalized_items: list[dict] = []
    clarification_issues: list[ClarificationIssue] = []
    assumptions: list[AssumptionApplied] = []
    side_items_to_add: list[str] = []
    lowered_message = (latest_message or "").lower()

    for item in items:
        item_copy = dict(item)
        retained_tokens: list[str] = []
        for token in split_modifier_tokens(item_copy.get("modifier")):
            entry = match_lexicon_entry(token)
            if entry is None:
                retained_tokens.append(token)
                continue
            if _prefer_as_side_item(entry, lowered_message):
                side_items_to_add.extend([entry.canonical_name] * int(item_copy.get("quantity", 1) or 1))
                if entry.can_be_modifier:
                    clarification_issues.append(
                        ClarificationIssue(
                            kind="side_or_modifier",
                            item_name=str(item_copy.get("name", "item")),
                            token=entry.canonical_name,
                            clarification_message=f"Would you like {entry.canonical_name.lower()} as a side item, or on your {item_copy.get('name', 'item')}?",
                        )
                    )
            else:
                retained_tokens.append(token)

        item_copy["modifier"] = ", ".join(retained_tokens) if retained_tokens else None
        normalized_items.append(item_copy)

    assumptions.extend(_default_assumptions_from_message(lowered_message, normalized_items, side_items_to_add))

    for side_name in side_items_to_add:
        normalized_items.append({"name": side_name, "quantity": 1, "modifier": None})

    if _mentions_generic_drink(lowered_message) and not _has_specific_drink(normalized_items):
        clarification_issues.append(
            ClarificationIssue(
                kind="generic_drink",
                item_name="Drink",
                token="soda",
                clarification_message="Would you like regular soda or something else?",
            )
        )

    return normalized_items, _dedupe_clarifications(clarification_issues), _dedupe_assumptions(assumptions)


def match_lexicon_entry(text: str) -> LexiconEntry | None:
    normalized = _normalize_token(text)
    if not normalized:
        return None
    for entry in _LEXICON:
        if normalized in entry.aliases:
            return entry
    return None


def is_side_like_token(text: str) -> bool:
    entry = match_lexicon_entry(text)
    return bool(entry and entry.can_be_side_item)


def build_side_vs_modifier_prompt(token: str, item_name: str) -> str:
    return f"Would you like {token.lower()} as a side item, or on your {item_name}?"


def _default_assumptions_from_message(
    lowered_message: str,
    normalized_items: list[dict],
    side_items_to_add: list[str],
) -> list[AssumptionApplied]:
    assumptions: list[AssumptionApplied] = []
    if "fries" in lowered_message and not _contains_item(normalized_items, "Regular Fries") and "Regular Fries" not in side_items_to_add:
        side_items_to_add.append("Regular Fries")
        assumptions.append(
            AssumptionApplied(
                kind="default_fries",
                token="fries",
                assumed_value="Regular Fries",
                explanation="I added regular fries by default. Want cajun or lemon pepper instead?",
            )
        )
    return assumptions


def _contains_item(items: list[dict], name: str) -> bool:
    target = name.strip().lower()
    return any(str(item.get("name", "")).strip().lower() == target for item in items)


def _mentions_generic_drink(lowered_message: str) -> bool:
    words = set(lowered_message.split())
    return bool({"soda", "drink", "pop"} & words)


def _has_specific_drink(items: list[dict]) -> bool:
    # Current fallback heuristic: if user added any drink-like line item other than generic Soda.
    for item in items:
        name = str(item.get("name", "")).strip().lower()
        if not name:
            continue
        if "soda" in name and name != "soda":
            return True
        if name in {"coke", "sprite", "diet coke", "dr pepper", "pepsi"}:
            return True
    return False


def _prefer_as_side_item(entry: LexiconEntry, lowered_message: str) -> bool:
    if not entry.can_be_side_item:
        return False
    if entry.kind in {"side", "dressing"}:
        return True
    if any(hint in lowered_message for hint in _SIDE_HINTS):
        return True
    if not any(hint in lowered_message for hint in _BOUND_HINTS):
        return entry.kind in {"sauce", "drink_generic"}
    return False


def _normalize_token(text: str) -> str:
    lowered = str(text or "").strip().lower()
    lowered = re.sub(r"\s+", " ", lowered.replace("-", " "))
    if lowered.startswith("extra "):
        lowered = lowered[6:]
    return lowered.strip()


def _dedupe_clarifications(issues: list[ClarificationIssue]) -> list[ClarificationIssue]:
    deduped: list[ClarificationIssue] = []
    seen: set[tuple[str, str, str]] = set()
    for issue in issues:
        key = (issue.kind, issue.item_name.strip().lower(), issue.token.strip().lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(issue)
    return deduped


def _dedupe_assumptions(values: list[AssumptionApplied]) -> list[AssumptionApplied]:
    deduped: list[AssumptionApplied] = []
    seen: set[tuple[str, str, str]] = set()
    for value in values:
        key = (value.kind, value.token.strip().lower(), value.assumed_value.strip().lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(value)
    return deduped
