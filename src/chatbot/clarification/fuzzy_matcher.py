from dataclasses import dataclass, field
from typing import Literal

from rapidfuzz import fuzz, process, utils

from src.chatbot.clarification.ai_resolver import resolve_ambiguous_match, resolve_not_found_item
from src.menu.loader import get_menu_context
from src.chatbot.schema import Message, OrderItem
from src.chatbot.clarification.constants import CONFIRMED_THRESHOLD, MODS_CONFIRMED_THRESHOLD, NOT_FOUND_THRESHOLD, LOW_MENU_MATCH_THRESHOLD, AMBIGUITY_GAP


@dataclass
class _MatchResult:
    item: OrderItem
    status: Literal["confirmed", "ambiguous", "not_found"]
    canonical_name: str | None = None
    candidates: list[str] = field(default_factory=list)
    clarification_message: str | None = None


@dataclass
class _FreeModifierMatch:
    status: Literal["confirmed", "ambiguous", "not_found"]
    canonical: str | None = None
    candidates: list[str] = field(default_factory=list)


class FuzzyMatcher:
    async def match_item(
        self,
        item: OrderItem,
        menu_aliases: list[tuple[str, str]],
        message_history: list[Message] | None = None,
        latest_message: str = "",
    ) -> _MatchResult:
        if not menu_aliases:
            return _MatchResult(item=item, status="not_found")

        canonical_names: list[str] = list(dict.fromkeys(c for _, c in menu_aliases))

        # Exact case-insensitive match always wins — skip fuzzy ambiguity checks
        for name in canonical_names:
            if name.lower() == item.name.lower():
                return _MatchResult(item=item, status="confirmed", canonical_name=name)

        print(item.name)

        alias_texts = [a for a, _ in menu_aliases]
        raw_matches = process.extract(
            item.name,
            alias_texts,
            scorer=_combined_scorer,
            limit=len(alias_texts),
        )  # [(alias_text, score, index), ...]

        # Collapse to best score per canonical name (a name-hit and a description-hit
        # both map to the same item; keep whichever scored higher).
        best_by_canonical: dict[str, float] = {}
        for _, score, idx in raw_matches:
            canonical = menu_aliases[idx][1]
            prev = best_by_canonical.get(canonical)
            if prev is None or score > prev:
                best_by_canonical[canonical] = score

        top_matches: list[tuple[str, float]] = sorted(
            best_by_canonical.items(), key=lambda kv: kv[1], reverse=True
        )[:5]
        print(f"top_matches: {top_matches}")

        if not top_matches or top_matches[0][1] < LOW_MENU_MATCH_THRESHOLD:
            top_candidates = [m[0] for m in top_matches] if top_matches else []
            ai_message = await resolve_not_found_item(
                item_name=item.name,
                top_candidates=top_candidates,
                menu_context=get_menu_context(),
                latest_message=latest_message,
                message_history=message_history,
            )
            return _MatchResult(item=item, status="not_found", clarification_message=ai_message)

        best_score = top_matches[0][1]

        if best_score >= CONFIRMED_THRESHOLD:
            # Check for a tie — multiple items within AMBIGUITY_GAP of the best score
            close_matches = [m for m in top_matches if best_score - m[1] <= AMBIGUITY_GAP]
            if len(close_matches) > 1:
                candidates = [m[0] for m in close_matches]
                resolution = await resolve_ambiguous_match(
                    candidates, latest_message, message_history
                )
                if resolution.confident:
                    # Find the exact candidate string the AI chose (case-insensitive)
                    matched = next(
                        (c for c in candidates if c.lower() == (resolution.canonical or "").lower()),
                        candidates[0],
                    )
                    return _MatchResult(
                        item=item,
                        status="confirmed",
                        canonical_name=matched,
                    )
                return _MatchResult(
                    item=item,
                    status="ambiguous",
                    candidates=candidates,
                    clarification_message=resolution.clarification_message,
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

    def match_free_modifier(self, text: str, allowed: list[str]) -> _FreeModifierMatch:
        """Match free-text modifier against menu option names (same thresholds as menu item matching)."""
        if not allowed:
            return _FreeModifierMatch(status="confirmed", canonical=text.strip() or None)
        deduped = list(dict.fromkeys(allowed))
        t = text.strip()
        if not t:
            return _FreeModifierMatch(status="confirmed", canonical=None)
        for opt in deduped:
            if opt.lower() == t.lower():
                return _FreeModifierMatch(status="confirmed", canonical=opt)
        top_matches = process.extract(
            t,
            deduped,
            scorer=_combined_scorer,
            limit=5,
        )
        if not top_matches or top_matches[0][1] < NOT_FOUND_THRESHOLD:
            return _FreeModifierMatch(status="not_found")

        best_score = top_matches[0][1]
        if best_score >= CONFIRMED_THRESHOLD:
            close_matches = [m for m in top_matches if best_score - m[1] <= AMBIGUITY_GAP]
            if len(close_matches) > 1:
                return _FreeModifierMatch(
                    status="ambiguous",
                    candidates=[m[0] for m in close_matches],
                )
            return _FreeModifierMatch(
                status="confirmed",
                canonical=top_matches[0][0],
            )
        close_matches = [m for m in top_matches if best_score - m[1] <= AMBIGUITY_GAP]
        return _FreeModifierMatch(
            status="ambiguous",
            candidates=[m[0] for m in close_matches],
        )


def _combined_scorer(s1: str, s2: str, **kwargs: object) -> float:
    # WRatio internally uses partial_token_set_ratio, which inflates scores for strings
    # sharing short connector tokens ("n", "and", "with"). Build the composite manually,
    # excluding both token-set variants, to avoid false positives on food names.
    s1p = utils.default_process(s1)
    s2p = utils.default_process(s2)
    PARTIAL_SCALE = 0.9
    return max(
        fuzz.ratio(s1p, s2p, processor=None),
        fuzz.partial_ratio(s1p, s2p, processor=None) * PARTIAL_SCALE,
        fuzz.token_sort_ratio(s1p, s2p, processor=None),
        fuzz.partial_token_sort_ratio(s1p, s2p, processor=None) * PARTIAL_SCALE,
    )