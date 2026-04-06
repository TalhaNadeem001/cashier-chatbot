from dataclasses import dataclass, field
from typing import Literal

from rapidfuzz import fuzz, process, utils

from src.chatbot.schema import OrderItem

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


@dataclass
class _FreeModifierMatch:
    status: Literal["confirmed", "ambiguous", "not_found"]
    canonical: str | None = None
    candidates: list[str] = field(default_factory=list)


class FuzzyMatcher:
    def match_item(self, item: OrderItem, menu_names: list[str]) -> _MatchResult:
        if not menu_names:
            return _MatchResult(item=item, status="not_found")

        # Exact case-insensitive match always wins — skip fuzzy ambiguity checks
        for name in menu_names:
            if name.lower() == item.name.lower():
                return _MatchResult(item=item, status="confirmed", canonical_name=name)

        top_matches = process.extract(
            item.name,
            menu_names,
            scorer=_combined_scorer,
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
    s1p = utils.default_process(s1)
    s2p = utils.default_process(s2)
    return max(
        fuzz.WRatio(s1p, s2p, processor=None),
        fuzz.token_set_ratio(s1p, s2p, processor=None),
    )