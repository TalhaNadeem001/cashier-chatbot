import itertools
import json
import re
from datetime import datetime, timezone

import httpx
from rapidfuzz import process

from src.cache import (
    cache_delete,
    cache_get,
    cache_list_length,
    cache_list_range,
    cache_set,
)
from src.chatbot.cart.ai_client import classify_modifier_or_addon_request
from src.chatbot.clarification.ai_resolver import resolve_modifiers_for_item
from src.chatbot.clarification.constants import (
    AMBIGUITY_GAP,
    CONFIRMED_THRESHOLD,
    LOW_MENU_MATCH_THRESHOLD,
    MODS_CONFIRMED_THRESHOLD,
    NOT_FOUND_THRESHOLD,
)
from src.chatbot.clarification.fuzzy_matcher import _combined_scorer
from src.chatbot.exceptions import AIServiceError
from src.chatbot.llm_client import generate_text
from src import firebase as _firebase
from src.config import settings
from src.menu.clover_client import (
    add_clover_line_item,
    add_clover_modification,
    create_clover_empty_order,
    delete_clover_line_item,
    delete_clover_modification,
    delete_clover_order,
    ensure_fresh_clover_access_token,
    fetch_clover_menu,
    fetch_clover_modifiers,
    fetch_clover_order,
    update_clover_line_item,
    update_clover_order,
)

from src.chatbot.constants import (
    _CLOVER_CREDS_REDIS_TTL_SECONDS,
    _COOKING_PREFERENCE_HINTS,
    _COOKING_MODIFIER_HINTS,
    _DEFAULT_PICKUP_MINUTES,
    _SESSION_CLOVER_ORDER_REDIS_TTL_SECONDS,
    _SESSION_ORDER_DATA_REDIS_TTL_SECONDS,
    _SUMMARIZE_HISTORY_MAX_OUTPUT_TOKENS,
)

from src.chatbot.utils import _clover_creds_redis_key, _menu_cache_key, _session_clover_order_redis_key, _session_status_redis_key, _session_order_state_redis_key, _session_order_data_redis_key, _session_messages_redis_key, _session_history_summary_cache_key, _normalize_session_history_message
from src.chatbot.utils import _summary_prompt_messages, _serialize_cached_history_summary, _parse_cached_history_summary
from src.chatbot.utils import _normalize_menu, _persist_menu_items_cache, _menu_cache_age_seconds, _menu_snapshot_considered_fresh
from src.chatbot.utils import _normalize_order_line_items, _line_item_quantity, _extract_line_item_modification_records
from src.chatbot.utils import _item_not_found_result, _availability_result, _describe_update_changes, _pricing_breakdown_from_order 


async def _get_order_data(session_id: str, creds: dict, *, force_refresh: bool = False) -> dict:
    """Return cached Clover order data, fetching fresh when missing or force_refresh=True.

    Stores the full fetch_clover_order response as JSON in Redis under
    _session_order_data_redis_key(session_id). Pass force_refresh=True after
    any mutation to bypass the cache and re-populate it.
    """
    key = _session_order_data_redis_key(session_id)
    if not force_refresh:
        cached = await cache_get(key)
        if cached:
            print(f"[_get_order_data] cache hit for session_id={session_id!r}")
            return json.loads(cached)
    order_id = await get_order_id_for_session(session_id, creds)
    order_data = await fetch_clover_order(
        creds["token"], creds["merchant_id"], creds["base_url"], order_id
    )
    await cache_set(key, json.dumps(order_data), ttl=_SESSION_ORDER_DATA_REDIS_TTL_SECONDS)
    return order_data


async def _invalidate_order_data_cache(session_id: str) -> None:
    await cache_delete(_session_order_data_redis_key(session_id))


async def prepare_clover_data(db, settings, merchant_id: str) -> dict:
    """Fetch Clover credentials, refresh token if needed, and return an enriched creds dict.

    Adds ``base_url`` and ``token`` keys to the creds dict so callers can pass a
    single ``creds`` object everywhere.

    Cache behaviour:
    - On cache hit: loads creds from Redis, calls ensure_fresh_clover_access_token
      with doc_ref=None (skips Firestore write), updates Redis only when the token
      changed.
    - On cache miss: fetches Firestore doc, calls ensure_fresh_clover_access_token
      normally (writes refreshed token to Firestore), then stores the full creds
      dict in Redis with a TTL of _CLOVER_CREDS_REDIS_TTL_SECONDS (3 hours).
    """
    redis_key = _clover_creds_redis_key(merchant_id)

    cached_raw = await cache_get(redis_key)
    if cached_raw is not None:
        print(f"[prepare_clover_data] cache hit merchant_id={merchant_id!r}")
        creds = json.loads(cached_raw)
        base_url = str(creds.get("api_base_url") or settings.CLOVER_API_BASE_URL).rstrip("/")
        old_token = creds.get("access_token")
        token = await ensure_fresh_clover_access_token(
            creds,
            base_url,
            None,
            app_client_id=settings.CLOVER_APP_ID,
        )
        creds["base_url"] = base_url
        creds["token"] = token
        if token != old_token:
            print(f"[prepare_clover_data] token refreshed — updating Redis merchant_id={merchant_id!r}")
            await cache_set(redis_key, json.dumps(creds), ttl=_CLOVER_CREDS_REDIS_TTL_SECONDS)
        return creds

    print(f"[prepare_clover_data] cache miss — fetching Firestore doc merchant_id={merchant_id!r}")
    snapshot = await _clover_integration_doc(db, merchant_id)
    creds = snapshot.to_dict() or {}
    print(
        f"[prepare_clover_data] doc exists={snapshot.exists} "
        f"fields={list(creds.keys())} "
        f"has_access_token={bool(creds.get('access_token'))} "
        f"has_refresh_token={bool(creds.get('refresh_token'))} "
        f"has_client_id={bool(creds.get('client_id'))} "
        f"CLOVER_APP_ID_set={bool(settings.CLOVER_APP_ID)}"
    )
    base_url = str(creds.get("api_base_url") or settings.CLOVER_API_BASE_URL).rstrip("/")
    print(f"[prepare_clover_data] base_url={base_url!r}")
    token = await ensure_fresh_clover_access_token(
        creds,
        base_url,
        snapshot.reference,
        app_client_id=settings.CLOVER_APP_ID,
    )
    print(f"[prepare_clover_data] token_acquired=True merchant_id={creds.get('merchant_id')!r}")
    creds["base_url"] = base_url
    creds["token"] = token
    await cache_set(redis_key, json.dumps(creds), ttl=_CLOVER_CREDS_REDIS_TTL_SECONDS)
    print(f"[prepare_clover_data] creds cached in Redis merchant_id={merchant_id!r}")
    return creds

async def _clover_integration_doc(db, user_id: str):
    return await (
        db.collection("Users")
        .document(user_id)
        .collection("Integrations")
        .document("Clover")
        .get()
    )


async def _menu_items_cached_or_fresh(creds: dict) -> dict:
    merchant_id = creds["merchant_id"]
    cached = await cache_get(_menu_cache_key(merchant_id))
    age_seconds = await _menu_cache_age_seconds(merchant_id)
    if cached and _menu_snapshot_considered_fresh(age_seconds):
        return json.loads(cached)

    raw = await fetch_clover_menu(creds["token"], merchant_id, creds["base_url"])
    raw["modifiers"] = await fetch_clover_modifiers(
        creds["token"], merchant_id, creds["base_url"]
    )
    normalized_menu = await _normalize_menu(raw)
    await _persist_menu_items_cache(merchant_id, normalized_menu)
    return normalized_menu


async def findClosestMenuItems(
    item_name: str,
    details: str | None = None,
    merchant_id: str | None = None,  # noqa: ARG001 — reserved for future multi-tenant routing
    creds: dict | None = None,
) -> dict:
    """Resolve a raw item name from the user's message against the real menu.

    Use this tool whenever the user mentions a food item and you need to confirm
    it exists on the menu or find what they most likely meant.

    Args:
        item_name:  The item name exactly as the user said it (e.g. "chiken burgar",
                    "Chicken Sandwich", "wings"). Do NOT normalise or correct spelling
                    before passing — the fuzzy matcher handles that.
        details:    Any qualifier the user attached that may help narrow the match
                    (e.g. "lemon pepper", "large", "spicy"). Pass None when absent.
                    Used to re-rank candidates by scoring against each item's modifiers
                    after the initial name-based fuzzy match.

    Returns a dict with three fields:

        exact_match (dict | None)
            The full menu item dict when match_confidence is "exact", otherwise None.

        candidates (list[dict])
            The top 2-3 closest menu items (full dicts including price and modifiers).
            Always check this list — it is populated for both "exact" and "close".
            Empty only when match_confidence is "none".

        match_confidence ("exact" | "close" | "none")
            "exact"  → item_name is on the menu verbatim; use exact_match directly.
            "close"  → no verbatim match; inspect candidates and ask the user to confirm
                       which one they meant, or pick the top candidate if context is clear.
            "none"   → item not found on the menu; apologise and suggest browsing the menu.

    Decision guide for the agent:
        - "exact"  → proceed with exact_match, no confirmation needed
        - "close"  → show candidates[0] (and optionally candidates[1]) and ask the user
                     "Did you mean X?" before adding to the order
        - "none"   → tell the user the item isn't on the menu
    """
    print(
        "[findClosestMenuItems] start "
        f"item_name={item_name!r} details={details!r} merchant_id={merchant_id!r}"
    )
    _no_match = {"exact_match": None, "candidates": [], "match_confidence": "none"}

    try:
        resolved_creds = creds
        if resolved_creds is None:
            raise ValueError("creds must be provided")
        else:
            print(
                "[findClosestMenuItems] using provided creds "
                f"merchant_id={resolved_creds.get('merchant_id')!r}"
            )

        menu_items = await _menu_items_cached_or_fresh(resolved_creds)
        return _find_closest_menu_items_from_menu(
            item_name=item_name,
            details=details,
            menu_items=menu_items,
        )
    except Exception as exc:
        print(
            "[findClosestMenuItems] error "
            f"item_name={item_name!r} details={details!r} error={exc!r}"
        )
        return _no_match

_SODA_ALIASES: frozenset[str] = frozenset({
    # Coca-Cola family
    "coke", "coca cola", "coca-cola", "coke classic",
    "diet coke", "coke zero", "coke zero sugar",
    "cherry coke", "cherry coca cola",
    "vanilla coke", "vanilla coca cola",
    "mexican coke", "mexico coke", "glass bottle coke",
    "coke with lemon", "coke with lime",
    # Pepsi family
    "pepsi", "pepsi cola", "diet pepsi",
    "pepsi zero", "pepsi max", "pepsi zero sugar",
    "wild cherry pepsi",
    # Sprite / 7UP / lemon-lime
    "sprite", "sprite zero", "sprite zero sugar",
    "7up", "7 up", "seven up", "diet 7up", "diet 7 up",
    "sierra mist",
    # Dr Pepper
    "dr pepper", "dr. pepper", "doctor pepper",
    "diet dr pepper", "dr pepper zero",
    # Root beer
    "root beer", "a&w", "a&w root beer", "a and w", "a and w root beer",
    "mug root beer", "barqs", "barq's", "barqs root beer", "barq's root beer",
    "diet root beer",
    # Mountain Dew
    "mountain dew", "mtn dew", "mtn. dew", "mt dew", "dew",
    "diet mountain dew", "diet dew",
    # Fanta / Crush / orange soda
    "fanta", "fanta orange", "fanta grape", "fanta strawberry", "fanta pineapple",
    "orange soda", "crush", "orange crush", "grape crush",
    "grape soda", "strawberry soda",
    # Ginger ale
    "ginger ale", "canada dry", "schweppes ginger ale",
    "diet ginger ale",
    # Other common sodas
    "fresca", "mello yello", "big red", "sunkist", "squirt",
    "jarritos", "jarritos tamarind", "jarritos lime", "jarritos mandarin",
    "rc cola", "rc", "cheerwine",
    # Generic terms
    "soda", "pop", "soft drink", "cola", "cold drink",
    "fountain drink", "carbonated drink", "fizzy drink",
})

_SODA_CANONICAL = "can of pop"


def _find_closest_menu_items_from_menu(
    *,
    item_name: str,
    details: str | None,
    menu_items: dict,
) -> dict:
    _no_match = {"exact_match": None, "candidates": [], "match_confidence": "none"}
    items_by_name = menu_items.get("by_name", {})
    items_name_set = set(items_by_name)
    print(
        "[findClosestMenuItems] menu loaded "
        f"distinct_names={len(items_name_set)} by_id_keys={len(menu_items.get('by_id', {}))}"
    )

    # Soda alias pre-processing: if the customer named a soda variant and the menu
    # has no exact match for it but does have the canonical "can of pop" item,
    # rewrite item_name so fuzzy matching resolves correctly.
    normalized_input = item_name.lower().strip()
    can_of_pop_in_menu = _SODA_CANONICAL in items_by_name
    if (
        normalized_input in _SODA_ALIASES
        and _get_local_item(item_name, items_by_name) is None
        and can_of_pop_in_menu
    ):
        print(
            f"[findClosestMenuItems] soda alias matched "
            f"original={item_name!r} → rewriting to {_SODA_CANONICAL!r}"
        )
        item_name = _SODA_CANONICAL

    exact_match = _get_local_item(item_name, items_by_name)
    top_matches = process.extract(
        item_name, items_name_set, scorer=_combined_scorer, limit=5
    )
    preview = [(m[0], round(float(m[1]), 2)) for m in (top_matches or [])[:5]]
    print(
        "[findClosestMenuItems] after fuzzy extract "
        f"exact_match_is_none={exact_match is None} top_matches_preview={preview!r}"
    )

    if exact_match is not None:
        candidates = _build_candidates(top_matches, details, items_by_name)
        print(
            "[findClosestMenuItems] return exact "
            f"candidate_count={len(candidates)} exact_id={exact_match.get('id')!r}"
        )
        return {
            "exact_match": exact_match,
            "candidates": candidates,
            "match_confidence": "exact",
        }

    if not top_matches or top_matches[0][1] < LOW_MENU_MATCH_THRESHOLD:
        best_score = top_matches[0][1] if top_matches else None
        # Category fallback: try matching against category names before giving up
        items_by_category = menu_items.get("by_category", {})
        if items_by_category:
            best_cat = process.extractOne(item_name, set(items_by_category), scorer=_combined_scorer)
            if best_cat and best_cat[1] >= LOW_MENU_MATCH_THRESHOLD:
                matched_cat = best_cat[0]
                category_items = items_by_category[matched_cat]
                print(
                    "[findClosestMenuItems] return category_match "
                    f"category={matched_cat!r} item_count={len(category_items)} score={best_cat[1]!r}"
                )
                return {
                    "exact_match": None,
                    "candidates": category_items,
                    "match_confidence": "category_match",
                    "matched_category": matched_cat,
                }
        print(
            "[findClosestMenuItems] return none "
            f"reason={'no_top_matches' if not top_matches else 'below_threshold'} "
            f"best_score={best_score!r} threshold={LOW_MENU_MATCH_THRESHOLD!r}"
        )
        return _no_match

    best_score = top_matches[0][1]
    candidates = _build_candidates(top_matches, details, items_by_name)

    # If the top fuzzy match is high-confidence with no close competitor, auto-confirm
    # it as exact — mirrors FuzzyMatcher.match_item which confirms at CONFIRMED_THRESHOLD.
    # This handles plurals/typos like "chicken sandos" → "Chicken Sando".
    top_name = top_matches[0][0]
    close_competitors = [m for m in top_matches[1:] if best_score - m[1] <= AMBIGUITY_GAP]
    verbatim_match = top_name.lower() == item_name.lower().strip()
    if best_score >= CONFIRMED_THRESHOLD and (not close_competitors or verbatim_match):
        auto_exact = _get_local_item(top_name, items_by_name)
        if auto_exact is not None:
            reason = "verbatim" if verbatim_match else "auto-confirmed"
            print(
                f"[findClosestMenuItems] return exact ({reason}) "
                f"score={best_score!r} top_name={top_name!r}"
            )
            return {
                "exact_match": auto_exact,
                "candidates": candidates,
                "match_confidence": "exact",
            }

    # Size-family detection: if 2+ top candidates share the same base name after
    # stripping a leading "N Pc/pc/piece/pieces" prefix, return size_variant so the
    # agent asks which size rather than asking "did you mean X?".
    _SIZE_PREFIX_RE = re.compile(r'^\d+\s*(?:pc|pcs|piece|pieces)\s+', re.IGNORECASE)
    base_groups: dict[str, list[dict]] = {}
    for c in candidates:
        base = _SIZE_PREFIX_RE.sub('', c.get('name', '')).strip().lower()
        base_groups.setdefault(base, []).append(c)

    # Wing-type detection: if 2+ distinct size families exist in the candidates,
    # the customer named a family category (e.g. "wings") without specifying a type.
    # Return wing_type_ambiguous so the agent lists types before asking for a size.
    size_families = [base for base, members in base_groups.items()
                     if any(_SIZE_PREFIX_RE.match(m.get('name', '')) for m in members)]
    if len(size_families) >= 2:
        # Full scan: collect every menu item belonging to any detected size family
        # so the agent sees all variants, not just the top-3 fuzzy candidates.
        display_types = []
        all_family_members: list[dict] = []
        for base in size_families:
            family_items = [
                item
                for name, item_list in items_by_name.items()
                if _SIZE_PREFIX_RE.sub('', name).strip().lower() == base
                for item in item_list
            ]
            if family_items:
                all_family_members.extend(family_items)
                display_name = _SIZE_PREFIX_RE.sub('', family_items[0].get('name', '')).strip()
                display_types.append(display_name)
        print(
            "[findClosestMenuItems] return wing_type_ambiguous "
            f"types={display_types!r} total_members={len(all_family_members)}"
        )
        return {
            "exact_match": None,
            "candidates": all_family_members,
            "match_confidence": "wing_type_ambiguous",
            "wing_types": display_types,
        }

    size_family_base = next((base for base, members in base_groups.items() if len(members) >= 2), None)
    if size_family_base is not None:
        # Full scan: collect every menu item in this size family so the agent
        # sees all size options, not just the top-3 fuzzy candidates.
        family_members = [
            item
            for name, item_list in items_by_name.items()
            if _SIZE_PREFIX_RE.sub('', name).strip().lower() == size_family_base
            for item in item_list
        ]
        size_options = []
        for c in family_members:
            label = _SIZE_PREFIX_RE.match(c.get('name', ''))
            if label:
                size_options.append(label.group(0).strip())
        size_options.sort(key=lambda x: int(x.split()[0]))
        display_base = _SIZE_PREFIX_RE.sub('', family_members[0].get('name', '')).strip() if family_members else size_family_base
        print(
            "[findClosestMenuItems] return size_variant "
            f"base={display_base!r} options={size_options!r}"
        )
        return {
            "exact_match": None,
            "candidates": family_members,
            "match_confidence": "size_variant",
            "size_family_base": display_base,
            "size_options": size_options,
        }

    print(
        "[findClosestMenuItems] return close "
        f"candidate_count={len(candidates)} top_name={top_matches[0][0]!r}"
    )
    return {"exact_match": None, "candidates": candidates, "match_confidence": "close"}


def _find_best_word_subset_match(
    item_name: str,
    menu_items: dict,
) -> dict | None:
    """Try all ordered word subsequences of item_name to find the best-scoring
    menu match when the full query returned matchConfidence 'none'.

    Modifier words embedded anywhere in the query (leading, trailing, or middle)
    inflate the query length beyond the menu item name, collapsing the
    partial_ratio score below LOW_MENU_MATCH_THRESHOLD. This function finds the
    highest-scoring subsequence of query words that matches a menu item, regardless
    of where the non-item words appear.

    The caller is responsible for treating excluded words as modifier hints.
    Because validateRequestedItem already computes leftover_words from the
    original itemName vs the matched item name, the excluded words are captured
    automatically and merged into unified_details without extra handling here.

    Returns None if no subset scores at or above LOW_MENU_MATCH_THRESHOLD.
    Returns a dict with:
        match_result   – result from _find_closest_menu_items_from_menu
        excluded_words – list[str] words absent from the winning subset (original order)
    """
    words = item_name.lower().strip().split()
    n = len(words)
    if n < 2:
        return None

    items_name_set = set(menu_items.get("by_name", {}))
    best_score = -1.0
    best_indices: tuple[int, ...] | None = None

    # Enumerate all ordered subsets (subsequences preserving word order) from
    # length n-1 down to 2. Longer subsets are tried first so ties are broken
    # in favour of the subset that drops the fewest words.
    for subset_len in range(n - 1, 1, -1):
        for indices in itertools.combinations(range(n), subset_len):
            candidate = " ".join(words[i] for i in indices)
            top = process.extractOne(candidate, items_name_set, scorer=_combined_scorer)
            if top is None:
                continue
            score = float(top[1])
            if score >= LOW_MENU_MATCH_THRESHOLD and score > best_score:
                best_score = score
                best_indices = indices

    if best_indices is None:
        print(
            "[validateRequestedItem] subset_match no subset above threshold "
            f"item_name={item_name!r}"
        )
        return None

    excluded_indices = sorted(set(range(n)) - set(best_indices))
    excluded_words = [words[i] for i in excluded_indices]
    winning_name = " ".join(words[i] for i in best_indices)
    print(
        "[validateRequestedItem] subset_match winner "
        f"winning_name={winning_name!r} score={best_score:.1f} "
        f"excluded_words={excluded_words!r}"
    )

    match_result = _find_closest_menu_items_from_menu(
        item_name=winning_name,
        details=None,
        menu_items=menu_items,
    )
    return {
        "match_result": match_result,
        "excluded_words": excluded_words,
    }


async def find_closest_menu_items(
    item_name: str,
    details: str | None = None,
    merchant_id: str | None = None,
    creds: dict | None = None,
) -> dict:
    return await findClosestMenuItems(
        item_name=item_name,
        details=details,
        merchant_id=merchant_id,
        creds=creds,
    )


def _get_local_item(name: str, items_by_name: dict) -> dict | None:
    value = items_by_name.get(name.lower().strip())
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _item_modifier_groups(item_row: dict) -> list[dict]:
    """Return one normalized modifier-group list for either raw Clover or cached menu rows."""
    groups: list[dict] = []

    normalized_groups = item_row.get("modifier_groups")
    if isinstance(normalized_groups, list):
        for group in normalized_groups:
            if not isinstance(group, dict):
                continue
            modifiers: list[dict] = []
            for modifier in group.get("modifiers", []):
                if not isinstance(modifier, dict):
                    continue
                modifiers.append(
                    {
                        "id": str(modifier.get("id", "")).strip(),
                        "name": str(modifier.get("name", "")).strip(),
                        "price": modifier.get("price", 0) or 0,
                    }
                )
            groups.append(
                {
                    "id": str(group.get("id", "")).strip(),
                    "name": str(group.get("name", "")).strip(),
                    "min_required": int(group.get("min_required", 0) or 0),
                    "max_allowed": int(group.get("max_allowed", 0) or 0),
                    "modifiers": modifiers,
                }
            )
        return groups

    for group in item_row.get("modifierGroups", {}).get("elements", []):
        if not isinstance(group, dict):
            continue
        modifiers: list[dict] = []
        for modifier in group.get("modifiers", {}).get("elements", []):
            if not isinstance(modifier, dict):
                continue
            modifiers.append(
                {
                    "id": str(modifier.get("id", "")).strip(),
                    "name": str(modifier.get("name", "")).strip(),
                    "price": modifier.get("price", 0) or 0,
                }
            )
        groups.append(
            {
                "id": str(group.get("id", "")).strip(),
                "name": str(group.get("name", "")).strip(),
                "min_required": int(group.get("minRequired", 0) or 0),
                "max_allowed": int(group.get("maxAllowed", 0) or 0),
                "modifiers": modifiers,
            }
        )

    return groups


def _modifier_names(item_def: dict) -> list[str]:
    return [
        m["name"]
        for group in _item_modifier_groups(item_def)
        for m in group.get("modifiers", [])
        if m.get("name")
    ]


def _score_details_against_item(details: str, item_def: dict) -> float:
    """Return the best _combined_scorer score of details against any modifier name in item_def.
    Returns 0.0 when the item has no modifiers or no match is found.
    """
    names = _modifier_names(item_def)
    if not names:
        return 0.0
    best = process.extractOne(details, names, scorer=_combined_scorer)
    return best[1] if best else 0.0


def _build_candidates(
    top_matches: list, details: str | None, items_by_name: dict
) -> list[dict]:
    candidates = [
        defn
        for name, _, _ in top_matches[:3]
        if (defn := _get_local_item(name, items_by_name)) is not None
    ]
    if not details:
        return candidates
    scored = [(c, _score_details_against_item(details, c)) for c in candidates]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [c for c, _ in scored]


async def check_item_availability(
    item_id: str,
    merchant_id: str | None = None,  # noqa: ARG001 — reserved for future multi-tenant routing
) -> dict:
    """Check whether a Clover inventory item can be ordered right now.

    Call after you know the concrete ``itemId`` (e.g. from ``findClosestMenuItems``).
    Reads the same Redis menu snapshot when possible so live Clover calls are minimised.

    Args:
        item_id:     Clover item UUID (string) exactly as returned on menu rows.
        merchant_id: Reserved for future multi-tenant routing; currently unused.

    Returns a dict with four fields:

        Available (bool)
            True when the item is sellable (not deleted, not hidden, ``available`` not false).

        itemId (str)
            Echo of ``item_id`` for the caller.

        itemName (str)
            Display name from the menu row; empty string when the item is unknown.

        unavailableReason (str | None)
            Short human-readable reason when ``Available`` is False;
            ``null`` when ``Available`` is True.

    Decision guide for the agent:
        - ``Available`` True  → proceed to add the line item.
        - ``Available`` False → tell the guest the item cannot be ordered and surface ``unavailableReason``.
        - ``itemName`` empty and reason is ``item not found on menu`` → refresh menu context or spelling.
    """
    print(
        "[check_item_availability] start "
        f"item_id={item_id!r} merchant_id={merchant_id!r}"
    )
    db = _firebase.firebaseDatabase
    creds = await prepare_clover_data(db, settings)

    menu_items = await _menu_items_cached_or_fresh(creds)
    by_id = menu_items.get("by_id", {})
    print(
        "[check_item_availability] menu loaded "
        f"by_id_count={len(by_id)}"
    )
    row = by_id.get(item_id)
    row_name = row.get("name") if row else None
    row_avail = row.get("available") if row else None
    print(
        "[check_item_availability] row lookup "
        f"found={row is not None} name={row_name!r} available_field={row_avail!r}"
    )

    if not row:
        print("[check_item_availability] return not_found")
        return _item_not_found_result(item_id)

    out = _availability_result(
        available=True,
        item_id=item_id,
        item_name=str(row.get("name", "")),
        unavailable_reason=None,
    )
    print(f"[check_item_availability] return available result={out!r}")
    return out


async def get_item_details(
    item_id: str,
    merchant_id: str | None = None,  # noqa: ARG001 — reserved for future multi-tenant routing
) -> dict:
    """Return display details for one resolved Clover menu item.

    Use this after the agent has already resolved an exact ``item_id`` and needs
    customer-facing item details for a menu question or follow-up explanation.
    Reads the cached normalized menu when possible.

    Args:
        item_id: Clover item UUID exactly as returned on menu rows.
        merchant_id: Merchant id for the current execution context. When provided
            and it does not match the resolved Clover merchant, the tool fails closed.

    Returns a dict:

        id (str | None)
            Clover item UUID when found.

        name (str | None)
            Display name when found.

        description (str | None)
            Alternate / description text when present.

        price (int | None)
            Price in cents when present.

        modifier_groups (list)
            Modifier group definitions attached to this item.

        categories (dict | None)
            Category membership from Clover.

        available (bool | None)
            Whether the item is currently orderable.

    Decision guide for the agent:
        - ``available`` False with empty / missing fields → treat as unavailable or unresolved.
        - ``available`` True or None → use name / price / description to answer the menu question.
        - Empty / missing values → ask the customer to clarify the item.
    """
    print(
        "[getItemDetails] start "
        f"item_id={item_id!r} merchant_id={merchant_id!r}"
    )
    db = _firebase.firebaseDatabase
    creds = await prepare_clover_data(db, settings)
    print(
        "[getItemDetails] after prepare_clover_data "
        f"creds_merchant_id={creds.get('merchant_id')!r}"
    )

    if merchant_id is not None and merchant_id != creds.get("merchant_id"):
        result = {"available": False}
        print(
            "[getItemDetails] return merchant_mismatch "
            f"result={result!r}"
        )
        return result

    menu_items = await _menu_items_cached_or_fresh(creds)
    by_id = menu_items.get("by_id", {})
    print("[getItemDetails] menu loaded " f"by_id_count={len(by_id)}")
    row = by_id.get(item_id)
    print(
        "[getItemDetails] row lookup "
        f"found={row is not None} name={(row.get('name') if row else None)!r}"
    )

    if not row:
        result = {"available": False}
        print(f"[getItemDetails] return not_found result={result!r}")
        return result

    result = {
        "id": row.get("id"),
        "name": row.get("name"),
        "description": row.get("alternateName"),
        "price": row.get("price"),
        "modifier_groups": _item_modifier_groups(row),
        "categories": row.get("categories"),
        "available": row.get("available"),
    }
    print(f"[getItemDetails] result={result!r}")
    return result


def _flatten_item_modifier_options(item_row: dict) -> list[dict]:
    options: list[dict] = []
    for group in _item_modifier_groups(item_row):
        group_id = group.get("id", "")
        group_name = group.get("name", "")
        min_required = int(group.get("min_required", 0) or 0)
        max_allowed = int(group.get("max_allowed", 0) or 0)
        for modifier in group.get("modifiers", []):
            modifier_id = str(modifier.get("id", "")).strip()
            modifier_name = str(modifier.get("name", "")).strip()
            if not modifier_id or not modifier_name:
                continue
            options.append(
                {
                    "groupId": group_id,
                    "groupName": group_name,
                    "modifierId": modifier_id,
                    "name": modifier_name,
                    "price": modifier.get("price", 0) or 0,
                    "minRequired": min_required,
                    "maxAllowed": max_allowed,
                }
            )
    return options


def _match_requested_modifier(requested: str, options: list[dict]) -> dict | None:
    if not options:
        return None

    choices = {index: option["name"] for index, option in enumerate(options)}
    match = process.extractOne(requested, choices, scorer=_combined_scorer)
    if match is None or match[1] < MODS_CONFIRMED_THRESHOLD:
        return None

    return options[match[2]]


def _required_modifier_groups(
    item_row: dict, selected_keys: set[tuple[str, str]]
) -> list[dict]:
    required_groups: list[dict] = []

    for group in _item_modifier_groups(item_row):
        min_required = int(group.get("min_required", 0) or 0)
        if min_required <= 0:
            continue

        group_id = group.get("id", "")
        group_name = group.get("name", "")
        selected_count = len(
            {
                modifier_id
                for selected_group_id, modifier_id in selected_keys
                if selected_group_id == group_id
            }
        )
        if selected_count >= min_required:
            continue

        required_groups.append(
            {
                "id": group_id,
                "name": group_name,
                "minRequired": min_required,
                "maxAllowed": int(group.get("max_allowed", 0) or 0),
                "remainingRequired": min_required - selected_count,
                "modifiers": [
                    {
                        "id": str(modifier.get("id", "")).strip(),
                        "name": str(modifier.get("name", "")).strip(),
                        "price": modifier.get("price", 0) or 0,
                    }
                    for modifier in group.get("modifiers", [])
                    if str(modifier.get("id", "")).strip()
                    and str(modifier.get("name", "")).strip()
                ],
            }
        )

    return required_groups


def _failed_modifier_validation_result(requested_modifications: list[str]) -> dict:
    invalid = [value.strip() for value in requested_modifications if str(value).strip()]
    return {
        "valid": [],
        "invalid": invalid,
        "requireChoice": [],
        "allValid": False,
    }


def _modifier_or_addon_negative_result() -> dict:
    return {
        "isAddon": False,
        "classification": "not_addon",
        "closestModifier": None,
        "suggestedNote": None,
    }


def _clean_modifier_request(value: str | None) -> str:
    return " ".join(str(value or "").split()).strip()


def _modifier_or_addon_candidates(requested: str, options: list[dict]) -> list[dict]:
    if not requested or not options:
        return []

    choices = {index: option["name"] for index, option in enumerate(options)}
    matches = process.extract(
        requested,
        choices,
        scorer=_combined_scorer,
        limit=5,
    )

    candidates: list[dict] = []
    seen_modifier_ids: set[str] = set()
    for _, score, option_index in matches:
        if score < NOT_FOUND_THRESHOLD:
            continue
        option = options[option_index]
        modifier_id = option["modifierId"]
        if modifier_id in seen_modifier_ids:
            continue
        seen_modifier_ids.add(modifier_id)
        candidates.append(
            {
                "modifierId": modifier_id,
                "name": option["name"],
                "groupId": option["groupId"],
                "groupName": option["groupName"],
                "score": score,
            }
        )

    if candidates:
        return candidates

    requested_lower = requested.lower()
    if not any(hint in requested_lower for hint in _COOKING_PREFERENCE_HINTS):
        return []

    for option in options:
        option_haystack = f"{option['name']} {option['groupName']}".lower()
        if not any(hint in option_haystack for hint in _COOKING_MODIFIER_HINTS):
            continue
        candidates.append(
            {
                "modifierId": option["modifierId"],
                "name": option["name"],
                "groupId": option["groupId"],
                "groupName": option["groupName"],
                "score": NOT_FOUND_THRESHOLD,
            }
        )
        if len(candidates) >= 5:
            break

    return candidates


def _modifier_reference_fields(reference: object) -> tuple[str, str]:
    if reference is None:
        return "", ""
    if isinstance(reference, dict):
        return (
            str(reference.get("modifierId", "")).strip(),
            str(reference.get("name", "")).strip(),
        )
    return (
        str(getattr(reference, "modifierId", "")).strip(),
        str(getattr(reference, "name", "")).strip(),
    )


def _resolve_modifier_reference(reference: object, options: list[dict]) -> dict | None:
    modifier_id, name = _modifier_reference_fields(reference)

    if modifier_id:
        for option in options:
            if option["modifierId"] == modifier_id:
                return {
                    "modifierId": option["modifierId"],
                    "name": option["name"],
                }

    if name:
        name_matches = [
            option for option in options if option["name"].lower() == name.lower()
        ]
        if len(name_matches) == 1:
            return {
                "modifierId": name_matches[0]["modifierId"],
                "name": name_matches[0]["name"],
            }

    return None


def _compose_modifier_addon_note(
    classification: str | None,
    closest_modifier: dict | None,
    requested_modification: str,
) -> str | None:
    if not classification or classification == "not_addon" or closest_modifier is None:
        return None
    if requested_modification.lower() == closest_modifier["name"].strip().lower():
        return None
    return f"{closest_modifier['name']}: {requested_modification}"


def _validated_modifier_or_addon_result(
    classification_result: object,
    requested_modification: str,
    options: list[dict],
) -> dict:
    is_modifier_or_addon = bool(
        getattr(classification_result, "isModifierOrAddon", False)
    )
    classification = getattr(classification_result, "classification", None)

    if not is_modifier_or_addon or classification in {None, "not_addon"}:
        return _modifier_or_addon_negative_result()

    closest_modifier = _resolve_modifier_reference(
        getattr(classification_result, "closestModifier", None),
        options,
    )
    if closest_modifier is None:
        return _modifier_or_addon_negative_result()

    return {
        "isAddon": True,
        "classification": classification,
        "closestModifier": closest_modifier,
        "suggestedNote": _compose_modifier_addon_note(
            classification,
            closest_modifier,
            requested_modification,
        ),
    }


async def validateModifications(
    itemId: str,
    merchantId: str,
    requestedModifications: list[str] | None = None,
) -> dict:
    """Validate raw requested modifications against one resolved Clover item.

    Use this when the execution agent already knows the concrete menu ``itemId``
    and wants to check whether one or more free-text modifier phrases can be
    safely converted into real Clover modifier ids before mutating the order.

    Args:
        itemId: Clover item UUID already resolved for the current order item.
        merchantId: Merchant id expected by the caller; validation fails closed
            when it does not match the resolved Clover merchant.
        requestedModifications: Raw modifier strings extracted from the guest message.

    Returns a dict with:
        valid: list of matched modifier rows with resolved ids, canonical names, and prices.
        invalid: raw modifier strings that could not be matched for this item.
        requireChoice: required modifier groups still missing one or more selections.
        allValid: True only when every non-empty request matched and no required group is missing.

    Decision guide for the agent:
        - ``allValid`` True with one clear ``valid`` row → safe to apply that modifier.
        - Non-empty ``invalid`` or ``requireChoice`` → ask the customer to clarify.
        - Empty ``valid`` with all requested values in ``invalid`` → fail closed; do not mutate the order.
    """
    requested = [
        str(value).strip()
        for value in (requestedModifications or [])
        if str(value).strip()
    ]
    print(
        "[validateModifications] start "
        f"itemId={itemId!r} merchantId={merchantId!r} requested={requested!r}"
    )

    db = _firebase.firebaseDatabase
    creds = await prepare_clover_data(db, settings)
    print(
        "[validateModifications] after prepare_clover_data "
        f"creds_merchant_id={creds.get('merchant_id')!r}"
    )

    if merchantId != creds.get("merchant_id"):
        result = _failed_modifier_validation_result(requested)
        print(
            "[validateModifications] return merchant_mismatch "
            f"result={result!r}"
        )
        return result

    menu_items = await _menu_items_cached_or_fresh(creds)
    print(
        "[validateModifications] menu loaded "
        f"by_id_count={len(menu_items.get('by_id', {}))}"
    )
    item_row = menu_items.get("by_id", {}).get(itemId)
    if not item_row:
        result = _failed_modifier_validation_result(requested)
        print(f"[validateModifications] return item_missing result={result!r}")
        return result

    flattened_options = _flatten_item_modifier_options(item_row)
    print(
        "[validateModifications] flattened options "
        f"count={len(flattened_options)}"
    )
    valid: list[dict] = []
    invalid: list[str] = []
    selected_keys: set[tuple[str, str]] = set()

    for raw_modification in requested:
        print(
            "[validateModifications] checking modification "
            f"raw={raw_modification!r}"
        )
        match = _match_requested_modifier(raw_modification, flattened_options)
        if match is None:
            invalid.append(raw_modification)
            print(
                "[validateModifications] no match "
                f"raw={raw_modification!r}"
            )
            continue

        selection_key = (match["groupId"], match["modifierId"])
        if selection_key in selected_keys:
            print(
                "[validateModifications] duplicate match skipped "
                f"selection_key={selection_key!r}"
            )
            continue

        selected_keys.add(selection_key)
        valid.append(
            {
                "requested": raw_modification,
                "modifierId": match["modifierId"],
                "name": match["name"],
                "price": match["price"],
                "groupId": match["groupId"],
                "groupName": match["groupName"],
            }
        )

    require_choice = _required_modifier_groups(item_row, selected_keys)
    result = {
        "valid": valid,
        "invalid": invalid,
        "requireChoice": require_choice,
        "allValid": not invalid and not require_choice,
    }
    print(f"[validateModifications] result={result!r}")
    return result


async def checkIfModifierOrAddOn(
    itemId: str,
    merchantId: str,
    requestedModification: str,
) -> dict:
    """Classify whether a free-text modification maps to a modifier or a note.

    Use this after exact modifier validation fails but the execution agent still
    needs to decide whether a free-text change should become a related note or
    a best-effort modifier attachment for one resolved menu item.

    Returns a dict with:
        isAddon: True when the request conceptually relates to one existing modifier.
        classification: One of quantity_variation, cooking_preference, ingredient_variation,
            or not_addon.
        closestModifier: The closest existing modifier reference with modifierId and name.
        suggestedNote: Cleaned note text when the request is related but not an exact modifier.

    Decision guide for the agent:
        - ``isAddon`` True with ``suggestedNote`` → safe to update the line-item note.
        - ``isAddon`` True with only ``closestModifier`` → optionally attach that modifier.
        - ``isAddon`` False → fail closed and ask the customer to clarify.
    """
    requested = _clean_modifier_request(requestedModification)
    print(
        "[checkIfModifierOrAddOn] start "
        f"itemId={itemId!r} merchantId={merchantId!r} requested={requested!r}"
    )
    if not requested:
        result = _modifier_or_addon_negative_result()
        print(f"[checkIfModifierOrAddOn] return empty_request result={result!r}")
        return result

    db = _firebase.firebaseDatabase
    creds = await prepare_clover_data(db, settings)
    print(
        "[checkIfModifierOrAddOn] after prepare_clover_data "
        f"creds_merchant_id={creds.get('merchant_id')!r}"
    )

    if merchantId != creds.get("merchant_id"):
        result = _modifier_or_addon_negative_result()
        print(
            "[checkIfModifierOrAddOn] return merchant_mismatch "
            f"result={result!r}"
        )
        return result

    menu_items = await _menu_items_cached_or_fresh(creds)
    print(
        "[checkIfModifierOrAddOn] menu loaded "
        f"by_id_count={len(menu_items.get('by_id', {}))}"
    )
    item_row = menu_items.get("by_id", {}).get(itemId)
    if not item_row:
        result = _modifier_or_addon_negative_result()
        print(f"[checkIfModifierOrAddOn] return item_missing result={result!r}")
        return result

    modifier_groups = _item_modifier_groups(item_row)
    flattened_options = _flatten_item_modifier_options(item_row)
    if not flattened_options:
        result = _modifier_or_addon_negative_result()
        print(f"[checkIfModifierOrAddOn] return no_options result={result!r}")
        return result

    candidates = _modifier_or_addon_candidates(requested, flattened_options)
    print(
        "[checkIfModifierOrAddOn] candidate search "
        f"candidate_count={len(candidates)}"
    )
    if not candidates:
        result = _modifier_or_addon_negative_result()
        print(f"[checkIfModifierOrAddOn] return no_candidates result={result!r}")
        return result

    try:
        classification_result = await classify_modifier_or_addon_request(
            item_name=str(item_row.get("name", "")).strip(),
            requested_modification=requested,
            candidate_modifiers=candidates,
            modifier_groups=modifier_groups,
        )
    except AIServiceError as exc:
        print(f"[checkIfModifierOrAddOn] classification_failed: {exc}")
        return _modifier_or_addon_negative_result()

    result = _validated_modifier_or_addon_result(
        classification_result,
        requested,
        flattened_options,
    )
    print(f"[checkIfModifierOrAddOn] result={result!r}")
    return result


async def validateRequestedItem(
    itemName: str,
    details: str | None = None,
    include_candidate_details: bool = False,
    merchant_id: str | None = None,  # noqa: ARG001 — reserved for future multi-tenant routing
    creds: dict | None = None,
) -> dict:
    """Resolve, validate, and classify a customer's item request in one call.

    Use this as the single entry point whenever the parsing agent has extracted
    an ``itemName`` (and optional ``details``) from the customer message and the
    execution agent needs to decide whether/how to add that item to the order.
    This replaces four sequential tool calls
    (findClosestMenuItems → getItemDetails → checkItemAvailability →
    validateModifications / checkIfModifierOrAddOn) with one.

    Args:
        itemName:
            The item name exactly as extracted from the customer message
            (e.g. "chiken burgar", "wings", "Chicken Sandwich").
            Do NOT normalise spelling before passing — the fuzzy matcher handles it.
        details:
            Any modifiers or qualifiers the customer attached
            (e.g. "lemon pepper, extra crispy").
            Pass ``None`` when absent. The string is split on commas and
            semicolons internally; do NOT pre-split.
        include_candidate_details:
            Controls whether the ``candidates`` array is populated when
            ``matchConfidence == "exact"``. Has NO effect for any other
            matchConfidence value — candidates are always returned in full
            for ``"close"``, ``"category_match"``, ``"wing_type_ambiguous"``,
            ``"size_variant"``, and ``"none"`` so the agent can present
            alternatives to the customer.
            When ``False`` (default) and matchConfidence is ``"exact"``,
            ``candidates`` is returned as an empty list. This is the safe
            default: on an exact match the agent should only read
            ``exactMatch.modifier_groups`` and never need candidate data.
            Pass ``True`` only if you have a specific reason to inspect
            alternative items after an exact match is already confirmed
            (rare — almost all flows should leave this at the default).

    Returns a dict with the following fields (all always present; ``None`` when
    the step was skipped because an earlier step returned a non-exact result):

        exactMatch (dict | None)
            Full menu item row when matchConfidence is ``"exact"``; else ``None``.

        candidates (list[dict])
            Top 2-3 fuzzy matches. Always populated in full for non-exact
            matchConfidence values. For ``"exact"``, populated only when
            ``include_candidate_details=True``; otherwise an empty list.

        matchConfidence ("exact" | "close" | "none" | "category_match" | "size_variant" | "wing_type_ambiguous")
            ``"exact"``              — item found verbatim; proceed with exactMatch.
            ``"close"``              — ambiguous; ask the customer to confirm which item
                                       they meant before adding.
            ``"none"``               — item not on the menu; tell the customer it is unavailable.
            ``"category_match"``     — phrase matched a category name (e.g. "wings" → "Wings").
                                       Extra field: ``matched_category`` (str) — category display name.
                                       ``candidates`` contains all items in that category.
            ``"size_variant"``       — phrase matched a size family without a size specified.
                                       Extra fields: ``size_family_base`` (str), ``size_options`` (list[str]).
            ``"wing_type_ambiguous"`` — phrase matched multiple wing-type families.
                                       Extra field: ``wing_types`` (list[str]).

        itemId (str | None)
            Clover item UUID; populated only when matchConfidence is ``"exact"``.

        merchantId (str | None)
            Clover merchant UUID; populated only when matchConfidence is ``"exact"``.

        available (bool | None)
            Whether the item is currently orderable.
            ``None`` when matchConfidence is not ``"exact"``.

        valid (list[dict] | None)
            Matched modifier rows (with modifierId, name, price, groupId,
            groupName, requested).
            ``None`` when the item is unavailable or matchConfidence is not ``"exact"``.

        invalid (list[str] | None)
            Raw modifier strings that could not be matched AND are not notes.
            ``None`` when the item is unavailable or matchConfidence is not ``"exact"``.

        asNote (list[str] | None)
            Modifier strings that failed exact matching but are valid free-text
            notes (checkIfModifierOrAddOn returned isAddon=True and suggestedNote).
            ``None`` when the item is unavailable or matchConfidence is not ``"exact"``.

        missingRequireChoice (list[dict] | None)
            Missing required modifier groups that still need a selection from the customer.
            ``None`` when the item is unavailable or matchConfidence is not ``"exact"``.

        allValid (bool | None)
            ``True`` only when ``invalid`` is empty and ``missingRequireChoice`` is empty.
            ``None`` when the item is unavailable or matchConfidence is not ``"exact"``.

        isModifierOrAddon (bool | None)
            Reserved; always ``None`` (matchConfidence ``"none"`` case is
            handled by the agent directly without a further tool call).

        classification (str | None)
            Reserved; always ``None``.

        closestModifier (dict | None)
            Reserved; always ``None``.

    Decision guide for the agent:

        matchConfidence == "none"
            Item not on the menu. Tell the customer the item is unavailable
            and suggest browsing the menu. All downstream fields are ``None``.

        matchConfidence == "category_match"
            The customer's phrase matched a menu category name (e.g. "wings",
            "burgers") but not a specific item. Extra field ``matched_category``
            (str) holds the display name of the matched category.
            ``candidates`` contains every item in that category.
            Do NOT call any mutation tools. List ALL candidates (name + price)
            and ask which one the customer wants. When they reply, re-call
            validateRequestedItem with just the chosen item name.

        matchConfidence == "close"
            Ambiguous match. Show ``candidates[0]`` (and optionally
            ``candidates[1]``) and ask "Did you mean X?" before adding.
            All downstream fields are ``None``.

        matchConfidence == "wing_type_ambiguous"
            The customer named a broad family (e.g. "wings") that spans multiple
            distinct item types on the menu (e.g. "Boneless Wings" and "Tenders").
            Extra field populated: ``wing_types`` (list[str]) — one display name
            per distinct type family.
            Do NOT call any mutation tools. List ALL wing_types and ask which type
            the customer wants. When they answer, re-call validateRequestedItem
            with just the type name (e.g. ``"boneless wings"``). That call will
            return size_variant — follow that rule to resolve the size.
            All downstream fields (itemId, available, valid, …) are ``None``.

        matchConfidence == "size_variant"
            The customer named a size family without specifying a size (e.g.
            "boneless wings" when the menu has "6 Pc", "12 Pc", etc.).
            Extra fields populated: ``size_family_base`` (str) and
            ``size_options`` (list[str]) — the label for each size variant.
            Do NOT call any mutation tools. List ALL size_options and ask the
            customer which size they want. When they answer, match their reply
            to the closest entry in size_options and re-call
            validateRequestedItem with the full reconstructed name
            (e.g. ``"12 Pc Boneless Wings"``) as itemName.
            All downstream fields (itemId, available, valid, …) are ``None``.

        matchConfidence == "exact" and available == False
            Item exists but cannot be ordered. Tell the customer it is
            currently unavailable. ``valid``/``invalid``/``asNote``/
            ``missingRequireChoice``/``allValid`` are all ``None``.

        matchConfidence == "exact" and available == True and allValid == True
            Safe to add the item. Use ``itemId`` and the ``valid`` modifier
            list (plus ``asNote`` strings as the line-item note) when calling
            addItemsToOrder.

        matchConfidence == "exact" and available == True and non-empty invalid
            One or more modifications could not be resolved. Ask the customer
            to clarify what they meant.

        matchConfidence == "exact" and available == True and non-empty missingRequireChoice
            One or more required modifier groups are missing a selection. Prompt
            the customer to choose from those groups before adding.

        matchConfidence == "exact" and available == True and non-empty asNote
            The modification is a note variant (e.g. "extra crispy"). Include
            those strings joined as the line-item note when calling addItemsToOrder.
    """
    print(
        "[validateRequestedItem] start "
        f"itemName={itemName!r} details={details!r} "
        f"include_candidate_details={include_candidate_details!r}"
    )

    _null_downstream: dict = {
        "itemId": None,
        "merchantId": None,
        "available": None,
        "valid": None,
        "invalid": None,
        "asNote": None,
        "missingRequireChoice": None,
        "allValid": None,
        "isModifierOrAddon": None,
        "classification": None,
        "closestModifier": None,
    }

    try:
        resolved_creds = creds
        if resolved_creds is None:
            raise ValueError("creds must be provided")
        else:
            print(
                "[validateRequestedItem] using provided creds "
                f"merchant_id={resolved_creds.get('merchant_id')!r}"
            )

        menu_items = await _menu_items_cached_or_fresh(resolved_creds)
        print(
            "[validateRequestedItem] menu loaded "
            f"by_id_count={len(menu_items.get('by_id', {}))}"
        )

        match_result = _find_closest_menu_items_from_menu(
            item_name=itemName,
            details=details,
            menu_items=menu_items,
        )
        exact_match = match_result.get("exact_match")
        candidates = match_result.get("candidates", [])
        match_confidence = match_result.get("match_confidence", "none")
        print(
            "[validateRequestedItem] match result "
            f"matchConfidence={match_confidence!r} "
            f"exactMatch_id={(exact_match.get('id') if exact_match else None)!r} "
            f"candidate_count={len(candidates)}"
        )

        # When the full query returns "none", modifier words embedded anywhere
        # in the query inflate its length past the menu item name, collapsing
        # the partial_ratio score below the threshold. Try all ordered word
        # subsequences to find the highest-scoring subset that matches a menu
        # item; excluded words are picked up automatically by leftover_words
        # below and merged into unified_details for the modifier resolver.
        if match_confidence == "none":
            subset = _find_best_word_subset_match(itemName, menu_items)
            if subset is not None:
                match_result = subset["match_result"]
                exact_match = match_result.get("exact_match")
                candidates = match_result.get("candidates", [])
                match_confidence = match_result.get("match_confidence", "none")
                print(
                    "[validateRequestedItem] subset_match applied "
                    f"matchConfidence={match_confidence!r} "
                    f"exactMatch_id={(exact_match.get('id') if exact_match else None)!r}"
                )

        base = {
            "exactMatch": exact_match,
            "candidates": candidates,
            "matchConfidence": match_confidence,
        }

        if match_confidence != "exact":
            print(
                "[validateRequestedItem] return early "
                f"matchConfidence={match_confidence!r}"
            )
            # Forward any extra fields from match_result (wing_types, size_options,
            # size_family_base, matched_category) so the agent can read them directly.
            extra_fields = {
                k: v for k, v in match_result.items()
                if k not in ("exact_match", "candidates", "match_confidence")
            }
            return {**base, **_null_downstream, **extra_fields}

        # --- exact match branch ---
        if not include_candidate_details:
            base["candidates"] = []

        item_id = str(exact_match.get("id", "")).strip()
        merchant_id = str(creds.get("merchant_id", "")).strip()
        by_id = menu_items.get("by_id", {})
        item_row = by_id.get(item_id) or exact_match

        available = bool(item_row.get("available", True))
        print(
            "[validateRequestedItem] availability check "
            f"itemId={item_id!r} available={available!r}"
        )

        if not available:
            print("[validateRequestedItem] return unavailable")
            return {
                **base,
                "itemId": item_id,
                "merchantId": merchant_id,
                "available": False,
                "valid": None,
                "invalid": None,
                "asNote": None,
                "missingRequireChoice": None,
                "allValid": None,
                "isModifierOrAddon": None,
                "classification": None,
                "closestModifier": None,
            }

        # --- available; validate modifiers ---
        flattened_options = _flatten_item_modifier_options(item_row)
        print(
            "[validateRequestedItem] flattened_options "
            f"count={len(flattened_options)}"
        )

        # Compute leftover words first — words in itemName that are not part of the
        # matched menu item name and may carry modifier intent (e.g. "spicy" in
        # "spicy chicken sando"). Done before any resolution so both sources can be
        # merged into a single resolver call.
        matched_name_words = set(str(item_row.get("name", "")).strip().lower().split())
        leftover_words = [w for w in itemName.lower().split() if w not in matched_name_words]
        print(
            "[validateRequestedItem] leftover_words "
            f"leftover={leftover_words!r}"
        )

        # Build unified details: explicit details + leftover words → one resolver pass.
        parts: list[str] = []
        if details:
            parts.append(details)
        if leftover_words:
            parts.append(", ".join(leftover_words))
        unified_details = ", ".join(parts) if parts else None

        if unified_details:
            resolution = await resolve_modifiers_for_item(
                details=unified_details,
                item_name=str(item_row.get("name", "")).strip(),
                available_options=flattened_options,
            )
            print(
                "[validateRequestedItem] ai_resolution "
                f"resolved_count={len(resolution.resolved)} "
                f"as_note={resolution.as_note!r} "
                f"unresolvable={resolution.unresolvable!r}"
            )

            option_by_id = {opt["modifierId"]: opt for opt in flattened_options}
            valid: list[dict] = []
            as_note: list[str] = list(resolution.as_note)
            truly_invalid: list[str] = list(resolution.unresolvable)
            selected_keys: set[tuple[str, str]] = set()

            for resolved_item in resolution.resolved:
                opt = option_by_id.get(resolved_item.modifierId)
                if opt is None:
                    print(
                        "[validateRequestedItem] ai_resolved_id_not_found "
                        f"modifierId={resolved_item.modifierId!r} name={resolved_item.name!r}"
                    )
                    truly_invalid.append(resolved_item.name)
                    continue
                selection_key = (opt["groupId"], opt["modifierId"])
                if selection_key in selected_keys:
                    print(
                        "[validateRequestedItem] ai_resolved_duplicate "
                        f"selection_key={selection_key!r}"
                    )
                    continue
                selected_keys.add(selection_key)
                valid.append(
                    {
                        "requested": resolved_item.name,
                        "modifierId": opt["modifierId"],
                        "name": opt["name"],
                        "price": opt["price"],
                        "groupId": opt["groupId"],
                        "groupName": opt["groupName"],
                    }
                )
        else:
            valid = []
            as_note = []
            truly_invalid = []
            selected_keys = set()

        missing_require_choice = _required_modifier_groups(item_row, selected_keys)
        all_valid = not truly_invalid and not missing_require_choice
        result = {
            **base,
            "itemId": item_id,
            "merchantId": merchant_id,
            "available": True,
            "valid": valid,
            "invalid": truly_invalid,
            "asNote": as_note,
            "missingRequireChoice": missing_require_choice,
            "allValid": all_valid,
            "isModifierOrAddon": None,
            "classification": None,
            "closestModifier": None,
        }
        print(f"[validateRequestedItem] result={result!r}")
        return result

    except Exception as exc:
        print(
            "[validateRequestedItem] error "
            f"itemName={itemName!r} details={details!r} error={exc!r}"
        )
        return {
            "exactMatch": None,
            "candidates": [],
            "matchConfidence": "none",
            **_null_downstream,
        }


async def addItemsToOrder(session_id: str, items: list[dict] | None = None, creds: dict | None = None) -> dict:
    """Add one or more menu items (with optional modifiers) to the customer's active Clover order.

    Call this when the customer confirms they want to add items to their order. Do NOT pass
    the Clover order id directly — use the ``session_id`` from the chat session; the function
    looks up (or creates) the order automatically.

    Args:
        session_id:
            The chat session identifier used to look up the active Clover order in Redis.
            Do not pass the raw Clover order id here.

        items:
            List of item specs to add, or ``None`` / empty list to just ensure an order
            exists without adding anything.

            Each spec is a dict with:
              - ``itemId``    (str, required)       — Clover item UUID from the menu.
              - ``quantity``  (int, optional)        — how many to add; defaults to 1.
              - ``modifiers`` (list[str], optional)  — list of Clover modifier UUIDs to apply.
              - ``note``      (str | None, optional) — free-text note for the line item.

    Returns a dict:

        success (bool)
            True only when ``failedItems`` is empty; False if any item or modifier failed.

        addedItems (list[dict])
            One entry per successfully added line item:
              - ``lineItemId``       (str)       — Clover line item id.
              - ``itemId``           (str)       — the item UUID that was added.
              - ``name``             (str)       — item display name from the menu.
              - ``quantity``         (int)       — quantity added.
              - ``modifiersApplied`` (list[str]) — modifier UUIDs that were successfully attached.
              - ``lineTotal``        (int)       — line price in cents from Clover response.

        failedItems (list[dict])
            One entry per item or modifier that could not be processed:
              - ``itemId``  (str) — the UUID that failed.
              - ``reason``  (str) — human-readable explanation.

        updatedOrderTotal (int)
            Current order total in cents after all additions; 0 when the fetch fails.

    Decision guide for the agent:
        - ``success`` True  → confirm all items added, quote ``updatedOrderTotal`` to the customer.
        - ``success`` False, addedItems non-empty → partial success; tell customer what was added
          and what failed (surface each ``failedItems[].reason``).
        - ``success`` False, addedItems empty → nothing was added; surface all failure reasons.
        - ``failedItems[].reason`` contains "ambiguous" → ask customer to clarify item vs modifier.
        - ``failedItems[].reason`` contains "not found" → item is not on the menu; suggest alternatives.
        - ``failedItems[].reason`` contains "modifier before" → modifiers must follow an item spec.
    """
    print(f"[addItemsToOrder] session_id={session_id!r} items={items}")
    if creds is None:
        raise ValueError("creds must be provided")
    print(
        f"[addItemsToOrder] merchant_id={creds.get('merchant_id')!r} base_url={creds.get('base_url')!r}"
    )

    order_id = await get_order_id_for_session(session_id, creds)
    print(f"[addItemsToOrder] order_id={order_id!r}")

    if not items:
        print("[addItemsToOrder] no items — returning early")
        return {
            "success": True,
            "addedItems": [],
            "failedItems": [],
            "updatedOrderTotal": 0,
        }

    menu = await _menu_items_cached_or_fresh(creds)
    by_id = menu.get("by_id", {})
    by_modifier_id = menu.get("by_modifier_id", {})
    print(
        f"[addItemsToOrder] menu loaded: {len(by_id)} items, {len(by_modifier_id)} modifier ids indexed"
    )

    added_items: list[dict] = []
    failed_items: list[dict] = []
    last_added_line_item_id: str | None = None

    for i, spec in enumerate(items):
        item_id = spec.get("itemId", "")
        quantity = spec.get("quantity") or 1
        modifiers: list[str] = spec.get("modifiers") or []
        note: str | None = spec.get("note")
        print(
            f"[addItemsToOrder] spec[{i}]: itemId={item_id!r} qty={quantity} modifiers={modifiers} note={note!r}"
        )

        in_by_id = item_id in by_id
        in_by_modifier_id = item_id in by_modifier_id

        # AMBIGUITY CHECK
        if in_by_id and in_by_modifier_id:
            print(
                f"[addItemsToOrder] AMBIGUOUS: {item_id!r} found in both by_id and by_modifier_id"
            )
            failed_items.append(
                {
                    "itemId": item_id,
                    "reason": f"ambiguous: id {item_id!r} exists as both a menu item and a modifier",
                }
            )
            continue

        # MODIFIER PATH
        if not in_by_id and in_by_modifier_id:
            print(
                f"[addItemsToOrder] MODIFIER PATH: {item_id!r} → attaching to line_item={last_added_line_item_id!r}"
            )
            if last_added_line_item_id is None:
                print(
                    f"[addItemsToOrder] FAIL: modifier {item_id!r} has no preceding line item"
                )
                failed_items.append(
                    {
                        "itemId": item_id,
                        "reason": "modifier before any item added in this call",
                    }
                )
                continue
            try:
                await add_clover_modification(
                    creds["token"],
                    creds["merchant_id"],
                    creds["base_url"],
                    order_id,
                    last_added_line_item_id,
                    item_id,
                )
                print(
                    f"[addItemsToOrder] modifier {item_id!r} applied to line {last_added_line_item_id!r}"
                )
                if added_items:
                    added_items[-1]["modifiersApplied"].append(item_id)
            except Exception as exc:
                print(f"[addItemsToOrder] modifier {item_id!r} failed: {exc!r}")
                failed_items.append({"itemId": item_id, "reason": str(exc)})
            continue

        # UNKNOWN ITEM
        if not in_by_id and not in_by_modifier_id:
            print(f"[addItemsToOrder] UNKNOWN: {item_id!r} not found in menu")
            failed_items.append(
                {
                    "itemId": item_id,
                    "reason": f"item not found on menu: {item_id!r}",
                }
            )
            continue

        # NORMAL PATH
        item_row = by_id[item_id]
        item_price: int = item_row.get("price") or 0
        print(
            f"[addItemsToOrder] NORMAL PATH: adding {item_id!r} qty={quantity} price={item_price} to order {order_id!r}"
        )
        try:
            for _ in range(quantity):
                response = await add_clover_line_item(
                    creds["token"],
                    creds["merchant_id"],
                    creds["base_url"],
                    order_id,
                    item_id,
                    1,
                    note,
                    item_price,
                )
                line_item_id = response["id"]
                print(
                    f"[addItemsToOrder] line item created: line_item_id={line_item_id!r} price={response.get('price')}"
                )
                modifiers_applied: list[str] = []

                for mod_id in modifiers:
                    try:
                        await add_clover_modification(
                            creds["token"],
                            creds["merchant_id"],
                            creds["base_url"],
                            order_id,
                            line_item_id,
                            mod_id,
                        )
                        print(
                            f"[addItemsToOrder] modifier {mod_id!r} applied to line {line_item_id!r}"
                        )
                        modifiers_applied.append(mod_id)
                    except Exception as exc:
                        print(
                            f"[addItemsToOrder] modifier {mod_id!r} on line {line_item_id!r} failed: {exc!r}"
                        )
                        failed_items.append({"itemId": mod_id, "reason": str(exc)})

                added_items.append(
                    {
                        "lineItemId": line_item_id,
                        "itemId": item_id,
                        "name": item_row.get("name", ""),
                        "quantity": 1,
                        "modifiersApplied": modifiers_applied,
                        "lineTotal": response.get("price", 0),
                    }
                )
                last_added_line_item_id = line_item_id

        except Exception as exc:
            print(f"[addItemsToOrder] item {item_id!r} failed: {exc!r}")
            failed_items.append({"itemId": item_id, "reason": str(exc)})

    print(
        f"[addItemsToOrder] loop done: {len(added_items)} added, {len(failed_items)} failed"
    )

    updated_total = 0
    try:
        order_data = await _get_order_data(session_id, creds, force_refresh=True)
        updated_total = order_data.get("total", 0) or 0
        print(f"[addItemsToOrder] updated order total: {updated_total} cents")
    except Exception as exc:
        print(f"[addItemsToOrder] fetch order data failed: {exc!r}")

    result = {
        "success": len(failed_items) == 0,
        "addedItems": added_items,
        "failedItems": failed_items,
        "updatedOrderTotal": updated_total,
    }
    print(f"[addItemsToOrder] result: {result}")
    return result


async def replaceItemInOrder(
    session_id: str,
    replacement: dict,
    lineItemId: str | None = None,
    orderPosition: int | None = None,
    itemName: str | None = None,
    creds: dict | None = None,
) -> dict:
    """Swap an already-ordered line item for a different menu item.

    Use this when the execution agent has already resolved the replacement to a
    concrete Clover ``itemId`` and needs to swap one current order line item for
    another (for example, "swap my fries for onion rings"). Always confirm the
    replacement with the customer before calling this tool.

    Target resolution (in priority order):
        1. ``lineItemId`` provided  → use directly; fail if not in current order.
        2. ``orderPosition`` provided → 1-indexed position in current line items; fail if out of range.
        3. ``itemName`` only → fuzzy-search current line item names; fail if no match or ambiguous.

    Args:
        session_id:
            The chat session identifier (same as used in addItemsToOrder).
            Do NOT pass the raw Clover order id.

        replacement:
            Dict describing the new item to add. Required fields:
              - ``itemId``    (str)              — Clover item UUID of the replacement item.
              - ``quantity``  (int, optional)    — how many to add; defaults to 1.
              - ``modifiers`` (list[str], optional) — Clover modifier UUIDs to apply.
              - ``note``      (str | None, optional) — free-text note.

        lineItemId:
            Clover line item id of the item to remove. Highest priority target resolver.
            Pass None when not known.

        orderPosition:
            1-indexed position of the item in the current order (e.g. 1 = first item).
            Used when lineItemId is not available. Pass None when not used.

        itemName:
            Display name of the item as the customer referred to it. Used only when
            neither lineItemId nor orderPosition is provided. Do NOT normalise spelling.

    Returns a dict:

        success (bool)
            True when the item was removed and the replacement was added successfully.

        removedItem (dict | None)
            ``{"name": str, "quantity": int}`` of the item that was removed;
            None when an error occurred before removal.

        addedItem (dict | None)
            ``{"name": str, "quantity": int, "modifiersApplied": list[str], "lineTotal": int}``
            of the newly added item; None when add failed or was not attempted.

        updatedOrderTotal (int)
            Current order total in cents; 0 when the fetch fails.

        error (str | None)
            Human-readable error message when ``success`` is False; None on success.

    Decision guide for the agent:
        - ``success`` True  → confirm the swap and quote ``updatedOrderTotal``.
        - ``success`` False, ``error`` contains "not found" → lineItemId / position / name did not
          match any current line item; ask customer to clarify which item they meant.
        - ``success`` False, ``error`` contains "add failed; attempted rollback" → replacement item
          could not be added; the original item has been re-added (best-effort).
        - ``success`` False, ``error`` contains "not on menu" → replacement itemId is unknown.
        - ``success`` False, other errors → surface ``error`` to the customer.
    """
    print(
        f"[replaceItemInOrder] session_id={session_id!r} lineItemId={lineItemId!r} orderPosition={orderPosition!r} itemName={itemName!r} replacement={replacement}"
    )
    if creds is None:
        raise ValueError("creds must be provided")

    order_id = await get_order_id_for_session(session_id, creds)
    print(f"[replaceItemInOrder] order_id={order_id!r}")

    # Fetch current order to resolve target
    order_data = await _get_order_data(session_id, creds)
    print(
        f"[replaceItemInOrder] raw order_data keys: {list(order_data.keys())}, lineItems type: {type(order_data.get('lineItems'))!r}"
    )
    raw_line_items = order_data.get("lineItems") or []
    if isinstance(raw_line_items, dict):
        line_items: list[dict] = raw_line_items.get("elements", [])
    elif isinstance(raw_line_items, list):
        line_items = raw_line_items
    else:
        line_items = []
    print(
        f"[replaceItemInOrder] current line items: {[li.get('id') for li in line_items]}"
    )

    # --- Target resolution ---
    target_line_item_id: str | None = None
    removed_name: str = ""
    removed_quantity: int = 1
    target_item_data: dict | None = None

    if lineItemId is not None:
        match = next((li for li in line_items if li.get("id") == lineItemId), None)
        if match is None:
            return {
                "success": False,
                "removedItem": None,
                "addedItem": None,
                "updatedOrderTotal": 0,
                "error": f"line item not found in order: {lineItemId!r}",
            }
        target_line_item_id = lineItemId
        target_item_data = match
        removed_name = match.get("name", "")
        removed_quantity = max(1, (match.get("unitQty") or 1000) // 1000)

    elif orderPosition is not None:
        idx = orderPosition - 1
        if idx < 0 or idx >= len(line_items):
            return {
                "success": False,
                "removedItem": None,
                "addedItem": None,
                "updatedOrderTotal": 0,
                "error": f"orderPosition {orderPosition} out of range (order has {len(line_items)} item(s))",
            }
        match = line_items[idx]
        target_line_item_id = match.get("id", "")
        target_item_data = match
        removed_name = match.get("name", "")
        removed_quantity = max(1, (match.get("unitQty") or 1000) // 1000)

    elif itemName is not None:
        # Fuzzy-search current line item names
        line_item_names = [li.get("name", "") for li in line_items]
        best = process.extractOne(itemName, line_item_names, scorer=_combined_scorer)
        if best is None or best[1] < LOW_MENU_MATCH_THRESHOLD:
            return {
                "success": False,
                "removedItem": None,
                "addedItem": None,
                "updatedOrderTotal": 0,
                "error": f"no line item matching {itemName!r} found in current order",
            }
        # Check for ambiguity (multiple matches close to the top score)
        top_matches = process.extract(
            itemName, line_item_names, scorer=_combined_scorer, limit=5
        )
        close = [m for m in top_matches if m[1] >= best[1] - 5]
        unique_names = {m[0] for m in close}
        if len(unique_names) > 1:
            return {
                "success": False,
                "removedItem": None,
                "addedItem": None,
                "updatedOrderTotal": 0,
                "error": f"ambiguous item name {itemName!r}; matches: {sorted(unique_names)}",
            }
        best_name = best[0]
        match = next((li for li in line_items if li.get("name", "") == best_name), None)
        if match is None:
            return {
                "success": False,
                "removedItem": None,
                "addedItem": None,
                "updatedOrderTotal": 0,
                "error": f"no line item matching {itemName!r} found in current order",
            }
        target_line_item_id = match.get("id", "")
        target_item_data = match
        removed_name = match.get("name", "")
        removed_quantity = max(1, (match.get("unitQty") or 1000) // 1000)

    else:
        return {
            "success": False,
            "removedItem": None,
            "addedItem": None,
            "updatedOrderTotal": 0,
            "error": "must provide one of: lineItemId, orderPosition, or itemName",
        }

    # --- Load menu ---
    menu = await _menu_items_cached_or_fresh(creds)
    by_id = menu.get("by_id", {})

    # --- Validate replacement item ---
    replacement_item_id: str = replacement.get("itemId", "")
    if replacement_item_id not in by_id:
        return {
            "success": False,
            "removedItem": None,
            "addedItem": None,
            "updatedOrderTotal": 0,
            "error": f"replacement item not on menu: {replacement_item_id!r}",
        }

    replacement_item_row = by_id[replacement_item_id]
    replacement_quantity: int = replacement.get("quantity") or 1
    replacement_modifiers: list[str] = replacement.get("modifiers") or []
    replacement_note: str | None = replacement.get("note")
    replacement_price: int = replacement_item_row.get("price") or 0

    # --- Delete target line item ---
    print(f"[replaceItemInOrder] deleting line item {target_line_item_id!r}")
    try:
        await delete_clover_line_item(
            creds["token"],
            creds["merchant_id"],
            creds["base_url"],
            order_id,
            target_line_item_id,
        )
    except Exception as exc:
        print(f"[replaceItemInOrder] delete failed: {exc!r}")
        return {
            "success": False,
            "removedItem": None,
            "addedItem": None,
            "updatedOrderTotal": 0,
            "error": f"failed to remove item: {exc}",
        }

    removed_item_info = {"name": removed_name, "quantity": removed_quantity}

    # --- Add replacement ---
    print(
        f"[replaceItemInOrder] adding replacement {replacement_item_id!r} qty={replacement_quantity}"
    )
    try:
        add_response = await add_clover_line_item(
            creds["token"],
            creds["merchant_id"],
            creds["base_url"],
            order_id,
            replacement_item_id,
            replacement_quantity,
            replacement_note,
            replacement_price,
        )
        new_line_item_id: str = add_response["id"]
        modifiers_applied: list[str] = []

        for mod_id in replacement_modifiers:
            try:
                await add_clover_modification(
                    creds["token"],
                    creds["merchant_id"],
                    creds["base_url"],
                    order_id,
                    new_line_item_id,
                    mod_id,
                )
                modifiers_applied.append(mod_id)
            except Exception as exc:
                print(f"[replaceItemInOrder] modifier {mod_id!r} failed: {exc!r}")

        added_item_info = {
            "name": replacement_item_row.get("name", ""),
            "quantity": replacement_quantity,
            "modifiersApplied": modifiers_applied,
            "lineTotal": add_response.get("price", 0),
        }

    except Exception as exc:
        print(
            f"[replaceItemInOrder] add replacement failed: {exc!r}; attempting rollback"
        )
        # Best-effort rollback: re-add the original item
        try:
            original_item_id = (
                target_item_data.get("item", {}).get("id") if target_item_data else None
            )
            if original_item_id:
                await add_clover_line_item(
                    creds["token"],
                    creds["merchant_id"],
                    creds["base_url"],
                    order_id,
                    original_item_id,
                    removed_quantity,
                    None,
                    None,
                )
                print(
                    f"[replaceItemInOrder] rollback succeeded: re-added {original_item_id!r}"
                )
            else:
                print(
                    "[replaceItemInOrder] rollback skipped: no original item id available"
                )
        except Exception as rb_exc:
            print(f"[replaceItemInOrder] rollback also failed: {rb_exc!r}")

        return {
            "success": False,
            "removedItem": removed_item_info,
            "addedItem": None,
            "updatedOrderTotal": 0,
            "error": "add failed; attempted rollback",
        }

    # --- Fetch updated total ---
    updated_total = 0
    try:
        updated_order = await _get_order_data(session_id, creds, force_refresh=True)
        updated_total = updated_order.get("total", 0) or 0
        print(f"[replaceItemInOrder] updated order total: {updated_total} cents")
    except Exception as exc:
        print(f"[replaceItemInOrder] fetch order data failed: {exc!r}")

    result = {
        "success": True,
        "removedItem": removed_item_info,
        "addedItem": added_item_info,
        "updatedOrderTotal": updated_total,
        "error": None,
    }
    print(f"[replaceItemInOrder] result: {result}")
    return result


async def removeItemFromOrder(session_id: str, target: dict, creds: dict | None = None) -> dict:
    """Fully remove a line item from the customer's current order.

    Use this when the customer wants to delete an item entirely (not reduce quantity).
    Always confirm removal with the customer before calling this tool.

    Target resolution (in priority order):
        1. ``target["orderPosition"]`` provided → 1-indexed position in current line items.
        2. ``target["itemName"]`` provided → fuzzy-search current line item names.
        At least one must be supplied or the call returns an error.

    When ``itemName`` is used, ALL line items sharing that name are removed.

    Args:
        session_id:
            The chat session identifier. Do NOT pass the raw Clover order id.

        target:
            Dict with at least one of:
              - ``orderPosition`` (int) — 1-indexed position in the current order.
              - ``itemName`` (str)      — Display name as the customer said it;
                                         do NOT normalise spelling.

    Returns a dict:

        success (bool)
            True when at least one item was deleted successfully.

        removedItem (dict | None)
            ``{"name": str, "quantity": int}`` of the removed item(s);
            None when an error occurred before deletion.

        removedCount (int)
            Total number of line items deleted. 1 when ``orderPosition`` or ``details``
            resolves to a specific item; N when a name-only removal deletes all matches.

        lineItemId (str | None)
            The specific Clover line item id that was deleted. Populated when
            ``orderPosition`` is used or when ``details`` resolves to a specific variant.
            None when a name-only removal deleted multiple items.

        remainingQuantity (int)
            Always 0 — partial removal is not supported.

        updatedOrderTotal (int)
            Current order total in cents after removal; 0 when the fetch fails.

        error (str | None)
            Human-readable error message when ``success`` is False; None on success.

    Decision guide for the agent:
        - ``success`` True → confirm removal, quote ``updatedOrderTotal``.
        - ``error`` contains "out of range" → invalid position; ask which item.
        - ``error`` contains "not found" → name match failed; ask for clarification.
        - ``error`` contains "ambiguous" → multiple name matches; ask to clarify.
        - ``error`` contains "must provide" → target dict is missing both keys.
        - Other errors → surface ``error`` to the customer.
    """
    print(f"[removeItemFromOrder] session_id={session_id!r} target={target}")

    order_position = target.get("orderPosition")
    item_name = target.get("itemName")

    if order_position is None and item_name is None:
        return {
            "success": False,
            "removedItem": None,
            "removedCount": 0,
            "lineItemId": None,
            "remainingQuantity": 0,
            "updatedOrderTotal": 0,
            "error": "must provide at least one of: orderPosition or itemName",
        }

    if creds is None:
        raise ValueError("creds must be provided")

    order_id = await get_order_id_for_session(session_id, creds)
    print(f"[removeItemFromOrder] order_id={order_id!r}")

    order_data = await _get_order_data(session_id, creds)
    print(
        f"[removeItemFromOrder] raw order_data keys: {list(order_data.keys())}, lineItems type: {type(order_data.get('lineItems'))!r}"
    )
    raw_line_items = order_data.get("lineItems") or []
    if isinstance(raw_line_items, dict):
        line_items: list[dict] = raw_line_items.get("elements", [])
    elif isinstance(raw_line_items, list):
        line_items = raw_line_items
    else:
        line_items = []
    print(
        f"[removeItemFromOrder] current line items: {[li.get('id') for li in line_items]}"
    )

    # --- Target resolution ---
    target_line_item_id: str | None = None
    item_display_name: str = ""
    removed_quantity: int = 1

    if order_position is not None:
        idx = order_position - 1
        if idx < 0 or idx >= len(line_items):
            return {
                "success": False,
                "removedItem": None,
                "removedCount": 0,
                "lineItemId": None,
                "remainingQuantity": 0,
                "updatedOrderTotal": 0,
                "error": f"orderPosition {order_position} out of range (order has {len(line_items)} item(s))",
            }
        match = line_items[idx]
        target_line_item_id = match.get("id", "")
        item_display_name = match.get("name", "")
        removed_quantity = max(1, (match.get("unitQty") or 1000) // 1000)

        # --- Delete single item (orderPosition path) ---
        print(f"[removeItemFromOrder] deleting line item {target_line_item_id!r}")
        try:
            await delete_clover_line_item(
                creds["token"],
                creds["merchant_id"],
                creds["base_url"],
                order_id,
                target_line_item_id,
            )
        except Exception as exc:
            print(f"[removeItemFromOrder] delete failed: {exc!r}")
            return {
                "success": False,
                "removedItem": None,
                "removedCount": 0,
                "lineItemId": None,
                "remainingQuantity": 0,
                "updatedOrderTotal": 0,
                "error": f"failed to remove item: {exc}",
            }

        # --- Fetch updated total (non-fatal) ---
        updated_total = 0
        try:
            updated_order = await _get_order_data(session_id, creds, force_refresh=True)
            updated_total = updated_order.get("total", 0) or 0
            print(f"[removeItemFromOrder] updated order total: {updated_total} cents")
        except Exception as exc:
            print(f"[removeItemFromOrder] fetch order data failed: {exc!r}")

        result = {
            "success": True,
            "removedItem": {"name": item_display_name, "quantity": removed_quantity},
            "removedCount": 1,
            "lineItemId": target_line_item_id,
            "remainingQuantity": 0,
            "updatedOrderTotal": updated_total,
            "error": None,
        }
        print(f"[removeItemFromOrder] result: {result}")
        return result

    else:
        # item_name is not None here
        line_item_names = [li.get("name", "") for li in line_items]
        best = process.extractOne(item_name, line_item_names, scorer=_combined_scorer)
        if best is None or best[1] < LOW_MENU_MATCH_THRESHOLD:
            return {
                "success": False,
                "removedItem": None,
                "removedCount": 0,
                "lineItemId": None,
                "remainingQuantity": 0,
                "updatedOrderTotal": 0,
                "error": f"no line item matching {item_name!r} found in current order",
            }
        top_matches = process.extract(
            item_name, line_item_names, scorer=_combined_scorer, limit=5
        )
        close = [m for m in top_matches if m[1] >= best[1] - 5]
        unique_names = {m[0] for m in close}
        if len(unique_names) > 1:
            return {
                "success": False,
                "removedItem": None,
                "removedCount": 0,
                "lineItemId": None,
                "remainingQuantity": 0,
                "updatedOrderTotal": 0,
                "error": f"ambiguous item name {item_name!r}; matches: {sorted(unique_names)}",
            }
        best_name = best[0]
        all_matching = [li for li in line_items if li.get("name", "") == best_name]
        if not all_matching:
            return {
                "success": False,
                "removedItem": None,
                "removedCount": 0,
                "lineItemId": None,
                "remainingQuantity": 0,
                "updatedOrderTotal": 0,
                "error": f"no line item matching {item_name!r} found in current order",
            }

        item_display_name = best_name

        # --- Determine which items to delete ---
        items_to_delete: list[dict] = all_matching
        specific_line_item_id: str | None = None

        # --- Delete ---
        removed_count = 0
        for li in items_to_delete:
            li_id = li.get("id", "")
            print(f"[removeItemFromOrder] deleting line item {li_id!r}")
            try:
                await delete_clover_line_item(
                    creds["token"],
                    creds["merchant_id"],
                    creds["base_url"],
                    order_id,
                    li_id,
                )
                removed_count += 1
            except Exception as exc:
                print(f"[removeItemFromOrder] delete failed for {li_id!r}: {exc!r}")

        if removed_count == 0:
            return {
                "success": False,
                "removedItem": None,
                "removedCount": 0,
                "lineItemId": None,
                "remainingQuantity": 0,
                "updatedOrderTotal": 0,
                "error": "failed to remove any matching items",
            }

        # --- Fetch updated total (non-fatal) ---
        updated_total = 0
        try:
            updated_order = await _get_order_data(session_id, creds, force_refresh=True)
            updated_total = updated_order.get("total", 0) or 0
            print(f"[removeItemFromOrder] updated order total: {updated_total} cents")
        except Exception as exc:
            print(f"[removeItemFromOrder] fetch order data failed: {exc!r}")

        result = {
            "success": True,
            "removedItem": {"name": item_display_name, "quantity": removed_count},
            "removedCount": removed_count,
            "lineItemId": specific_line_item_id,
            "remainingQuantity": 0,
            "updatedOrderTotal": updated_total,
            "error": None,
        }
        print(f"[removeItemFromOrder] result: {result}")
        return result


async def changeItemQuantity(session_id: str, target: dict, newQuantity: int, creds: dict | None = None) -> dict:
    """Change the quantity of an existing line item in the customer's order.

    Use this when the customer wants more or fewer of the same item and the agent
    has already resolved the request to a final absolute quantity. Do not call this
    tool for quantity zero; route that to ``removeItemFromOrder`` instead.

    Target resolution (in priority order):
        1. ``target["lineitemId"]`` or ``target["lineItemId"]`` provided → exact current line item id.
        2. ``target["orderPosition"]`` provided → 1-indexed position in current line items.
        3. ``target["itemName"]`` provided → fuzzy-search current line item names.
    
    newQuantity: int
        The requested final quantity that was passed to this tool.

    Returns a dict:

        success (bool)
            True when the quantity was updated successfully or was already the requested quantity.

        itemName (str)
            Display name of the matched line item, or empty string when no match was found.

        previousQuantity (int)
            The current quantity before any change; 0 when no target could be resolved.

        newQuantity (int)
            The requested final quantity that was passed to this tool.

        updatedOrderTotal (int)
            Current order total in cents after the update; for no-op success, this is the current total.
            Returns 0 when the follow-up order fetch fails or the mutation did not complete.

        error (str | None)
            Human-readable error message when ``success`` is False; None on success.
    """
    print(
        f"[changeItemQuantity] session_id={session_id!r} target={target} newQuantity={newQuantity!r}"
    )

    target_line_item_id = target.get("lineitemId") or target.get("lineItemId")
    order_position = target.get("orderPosition")
    item_name = target.get("itemName")

    if target_line_item_id is None and order_position is None and item_name is None:
        return {
            "success": False,
            "itemName": "",
            "previousQuantity": 0,
            "newQuantity": newQuantity,
            "updatedOrderTotal": 0,
            "error": "must provide one of: lineitemId, lineItemId, orderPosition, or itemName",
        }

    if creds is None:
        raise ValueError("creds must be provided")

    order_id = await get_order_id_for_session(session_id, creds)
    print(f"[changeItemQuantity] order_id={order_id!r}")

    order_data = await _get_order_data(session_id, creds)
    print(
        f"[changeItemQuantity] raw order_data keys: {list(order_data.keys())}, lineItems type: {type(order_data.get('lineItems'))!r}"
    )
    line_items = _normalize_order_line_items(order_data)
    print(
        f"[changeItemQuantity] current line items: {[li.get('id') for li in line_items]}"
    )

    matched_line_item: dict | None = None

    if target_line_item_id is not None:
        matched_line_item = next(
            (li for li in line_items if li.get("id") == target_line_item_id), None
        )
        if matched_line_item is None:
            return {
                "success": False,
                "itemName": "",
                "previousQuantity": 0,
                "newQuantity": newQuantity,
                "updatedOrderTotal": 0,
                "error": f"line item not found in order: {target_line_item_id!r}",
            }

    elif order_position is not None:
        idx = order_position - 1
        if idx < 0 or idx >= len(line_items):
            return {
                "success": False,
                "itemName": "",
                "previousQuantity": 0,
                "newQuantity": newQuantity,
                "updatedOrderTotal": 0,
                "error": f"orderPosition {order_position} out of range (order has {len(line_items)} item(s))",
            }
        matched_line_item = line_items[idx]

    else:
        line_item_names = [li.get("name", "") for li in line_items]
        best = process.extractOne(item_name, line_item_names, scorer=_combined_scorer)
        if best is None or best[1] < LOW_MENU_MATCH_THRESHOLD:
            return {
                "success": False,
                "itemName": "",
                "previousQuantity": 0,
                "newQuantity": newQuantity,
                "updatedOrderTotal": 0,
                "error": f"no line item matching {item_name!r} found in current order",
            }
        top_matches = process.extract(
            item_name, line_item_names, scorer=_combined_scorer, limit=5
        )
        close = [m for m in top_matches if m[1] >= best[1] - 5]
        unique_names = {m[0] for m in close}
        if len(unique_names) > 1:
            return {
                "success": False,
                "itemName": "",
                "previousQuantity": 0,
                "newQuantity": newQuantity,
                "updatedOrderTotal": 0,
                "error": f"ambiguous item name {item_name!r}; matches: {sorted(unique_names)}",
            }
        best_name = best[0]
        matched_line_item = next(
            (li for li in line_items if li.get("name", "") == best_name), None
        )
        if matched_line_item is None:
            return {
                "success": False,
                "itemName": "",
                "previousQuantity": 0,
                "newQuantity": newQuantity,
                "updatedOrderTotal": 0,
                "error": f"no line item matching {item_name!r} found in current order",
            }

    matched_name = matched_line_item.get("name", "")
    same_name_items = [li for li in line_items if li.get("name") == matched_name]
    previous_quantity = len(same_name_items)

    modifications = matched_line_item.get("modifications", {}).get("elements", [])
    modifier_ids = [
        m.get("modifier", {}).get("id")
        for m in modifications
        if m.get("modifier", {}).get("id")
    ]

    if newQuantity <= 0:
        return {
            "success": False,
            "itemName": matched_name,
            "previousQuantity": previous_quantity,
            "newQuantity": newQuantity,
            "updatedOrderTotal": 0,
            "error": "quantity must be greater than zero; use removeItemFromOrder to remove an item",
        }

    current_total = order_data.get("total", 0) or 0
    if newQuantity == previous_quantity:
        result = {
            "success": True,
            "itemName": matched_name,
            "previousQuantity": previous_quantity,
            "newQuantity": newQuantity,
            "updatedOrderTotal": current_total,
            "error": None,
        }
        print(f"[changeItemQuantity] no-op result: {result}")
        return result

    item_id = matched_line_item.get("item", {}).get("id", "")
    item_price = matched_line_item.get("price", 0) or 0

    if newQuantity > previous_quantity:
        to_add = newQuantity - previous_quantity
        print(f"[changeItemQuantity] adding {to_add} line item(s) for {matched_name!r}")
        try:
            for _ in range(to_add):
                response = await add_clover_line_item(
                    creds["token"], creds["merchant_id"], creds["base_url"],
                    order_id, item_id, 1, None, item_price
                )
                new_line_item_id = response["id"]
                for mod_id in modifier_ids:
                    await add_clover_modification(
                        creds["token"], creds["merchant_id"], creds["base_url"],
                        order_id, new_line_item_id, mod_id
                    )
        except Exception as exc:
            print(f"[changeItemQuantity] add failed: {exc!r}")
            return {
                "success": False,
                "itemName": matched_name,
                "previousQuantity": previous_quantity,
                "newQuantity": newQuantity,
                "updatedOrderTotal": 0,
                "error": f"failed to change item quantity: {exc}",
            }
    else:
        to_delete = previous_quantity - newQuantity
        print(f"[changeItemQuantity] deleting {to_delete} line item(s) for {matched_name!r}")
        try:
            for li in same_name_items[:to_delete]:
                await delete_clover_line_item(
                    creds["token"], creds["merchant_id"], creds["base_url"],
                    order_id, li["id"]
                )
        except Exception as exc:
            print(f"[changeItemQuantity] delete failed: {exc!r}")
            return {
                "success": False,
                "itemName": matched_name,
                "previousQuantity": previous_quantity,
                "newQuantity": newQuantity,
                "updatedOrderTotal": 0,
                "error": f"failed to change item quantity: {exc}",
            }

    updated_total = 0
    try:
        updated_order = await _get_order_data(session_id, creds, force_refresh=True)
        updated_total = updated_order.get("total", 0) or 0
        print(f"[changeItemQuantity] updated order total: {updated_total} cents")
    except Exception as exc:
        print(f"[changeItemQuantity] fetch order data failed: {exc!r}")

    result = {
        "success": True,
        "itemName": matched_name,
        "previousQuantity": previous_quantity,
        "newQuantity": newQuantity,
        "updatedOrderTotal": updated_total,
        "error": None,
    }
    print(f"[changeItemQuantity] result: {result}")
    return result


async def updateItemInOrder(session_id: str, target: dict, updates: dict, creds: dict | None = None) -> dict:
    """Update modifiers and/or the note for one existing order line item.

    Use this when the execution agent already knows which current order item to
    change and has resolved one safe mutation to apply, such as adding a modifier,
    removing a modifier, or writing a line-item note.

    Target resolution (in priority order):
        1. ``target["lineitemId"]`` or ``target["lineItemId"]`` provided → exact current line item id.
        2. ``target["orderPosition"]`` provided → 1-indexed position in current line items.
        3. ``target["itemName"]`` provided → fuzzy-search current line item names.

    The ``updates`` dict may contain:
        - ``addModifiers`` (list[str])      — modifier ids to add
        - ``removeModifiers`` (list[str])   — modifier ids to remove
        - ``note`` (str | None)             — set note when string, clear it when explicitly null

    Returns a dict:

        success (bool)
            True when the requested change was applied, or when no-op success is valid.

        itemName (str)
            The resolved current line-item name.

        appliedChanges (str)
            Human-readable summary of what changed.

        updatedOrderTotal (int)
            Current order total in cents after the change.

        error (str | None)
            Human-readable error message when ``success`` is False.

    Decision guide for the agent:
        - ``success`` True → confirm the item was updated.
        - ``success`` False with target-resolution errors → ask the customer which current item they meant.
        - ``success`` False with modifier / note mutation errors → surface the error and avoid assuming the change succeeded.
    """
    print(
        f"[updateItemInOrder] session_id={session_id!r} target={target} updates={updates}"
    )

    target_line_item_id = target.get("lineitemId") or target.get("lineItemId")
    order_position = target.get("orderPosition")
    item_name = target.get("itemName")

    if target_line_item_id is None and order_position is None and item_name is None:
        return {
            "success": False,
            "itemName": "",
            "appliedChanges": "",
            "updatedOrderTotal": 0,
            "error": "must provide one of: lineitemId, lineItemId, orderPosition, or itemName",
        }

    add_modifiers_raw = updates.get("addModifiers")
    remove_modifiers_raw = updates.get("removeModifiers")
    note_present = "note" in updates
    note_value = updates.get("note")

    add_modifiers = [str(mod_id) for mod_id in (add_modifiers_raw or []) if mod_id]
    remove_modifiers = [
        str(mod_id) for mod_id in (remove_modifiers_raw or []) if mod_id
    ]

    if not add_modifiers and not remove_modifiers and not note_present:
        return {
            "success": False,
            "itemName": "",
            "appliedChanges": "",
            "updatedOrderTotal": 0,
            "error": "updates must include at least one of: addModifiers, removeModifiers, or note",
        }

    modifier_conflicts = sorted(set(add_modifiers).intersection(remove_modifiers))
    if modifier_conflicts:
        return {
            "success": False,
            "itemName": "",
            "appliedChanges": "",
            "updatedOrderTotal": 0,
            "error": f"modifier ids cannot appear in both addModifiers and removeModifiers: {modifier_conflicts}",
        }

    if creds is None:
        raise ValueError("creds must be provided")

    order_id = await get_order_id_for_session(session_id, creds)
    print(f"[updateItemInOrder] order_id={order_id!r}")

    order_data = await _get_order_data(session_id, creds)
    print(
        f"[updateItemInOrder] raw order_data keys: {list(order_data.keys())}, lineItems type: {type(order_data.get('lineItems'))!r}"
    )
    line_items = _normalize_order_line_items(order_data)
    print(
        f"[updateItemInOrder] current line items: {[li.get('id') for li in line_items]}"
    )

    matched_line_item: dict | None = None

    if target_line_item_id is not None:
        matched_line_item = next(
            (li for li in line_items if li.get("id") == target_line_item_id), None
        )
        if matched_line_item is None:
            return {
                "success": False,
                "itemName": "",
                "appliedChanges": "",
                "updatedOrderTotal": 0,
                "error": f"line item not found in order: {target_line_item_id!r}",
            }

    elif order_position is not None:
        idx = order_position - 1
        if idx < 0 or idx >= len(line_items):
            return {
                "success": False,
                "itemName": "",
                "appliedChanges": "",
                "updatedOrderTotal": 0,
                "error": f"orderPosition {order_position} out of range (order has {len(line_items)} item(s))",
            }
        matched_line_item = line_items[idx]

    else:
        line_item_names = [li.get("name", "") for li in line_items]
        best = process.extractOne(item_name, line_item_names, scorer=_combined_scorer)
        if best is None or best[1] < LOW_MENU_MATCH_THRESHOLD:
            return {
                "success": False,
                "itemName": "",
                "appliedChanges": "",
                "updatedOrderTotal": 0,
                "error": f"no line item matching {item_name!r} found in current order",
            }
        top_matches = process.extract(
            item_name, line_item_names, scorer=_combined_scorer, limit=5
        )
        close = [m for m in top_matches if m[1] >= best[1] - 5]
        unique_names = {m[0] for m in close}
        if len(unique_names) > 1:
            return {
                "success": False,
                "itemName": "",
                "appliedChanges": "",
                "updatedOrderTotal": 0,
                "error": f"ambiguous item name {item_name!r}; matches: {sorted(unique_names)}",
            }
        best_name = best[0]
        matched_line_item = next(
            (li for li in line_items if li.get("name", "") == best_name), None
        )
        if matched_line_item is None:
            return {
                "success": False,
                "itemName": "",
                "appliedChanges": "",
                "updatedOrderTotal": 0,
                "error": f"no line item matching {item_name!r} found in current order",
            }

    matched_name = matched_line_item.get("name", "")
    matched_line_item_id = matched_line_item.get("id", "")
    if not matched_line_item_id:
        return {
            "success": False,
            "itemName": matched_name,
            "appliedChanges": "",
            "updatedOrderTotal": 0,
            "error": "cannot update item because the Clover line item id is missing",
        }

    modification_records = _extract_line_item_modification_records(matched_line_item)
    present_modifier_ids = {record["modifier_id"] for record in modification_records}

    modifiers_to_remove = [
        record
        for record in modification_records
        if record["modifier_id"] in set(remove_modifiers)
    ]
    modifiers_to_add = [
        modifier_id
        for modifier_id in add_modifiers
        if modifier_id not in present_modifier_ids
    ]

    current_note = matched_line_item.get("note")
    note_changed = note_present and note_value != current_note

    if not modifiers_to_remove and not modifiers_to_add and not note_changed:
        current_total = order_data.get("total", 0) or 0
        return {
            "success": True,
            "itemName": matched_name,
            "appliedChanges": "no changes applied",
            "updatedOrderTotal": current_total,
            "error": None,
        }

    removed_count = 0
    added_count = 0

    for record in modifiers_to_remove:
        print(
            "[updateItemInOrder] removing modification "
            f"{record['modification_id']!r} (modifier {record['modifier_id']!r})"
        )
        try:
            await delete_clover_modification(
                creds["token"],
                creds["merchant_id"],
                creds["base_url"],
                order_id,
                matched_line_item_id,
                record["modification_id"],
            )
            removed_count += 1
        except Exception as exc:
            print(f"[updateItemInOrder] remove modifier failed: {exc!r}")
            return {
                "success": False,
                "itemName": matched_name,
                "appliedChanges": _describe_update_changes(
                    removed=removed_count,
                    added=added_count,
                    note_action=None,
                ),
                "updatedOrderTotal": 0,
                "error": f"failed to remove modifier {record['modifier_id']!r}: {exc}",
            }

    for modifier_id in modifiers_to_add:
        print(f"[updateItemInOrder] adding modifier {modifier_id!r}")
        try:
            await add_clover_modification(
                creds["token"],
                creds["merchant_id"],
                creds["base_url"],
                order_id,
                matched_line_item_id,
                modifier_id,
            )
            added_count += 1
        except Exception as exc:
            print(f"[updateItemInOrder] add modifier failed: {exc!r}")
            return {
                "success": False,
                "itemName": matched_name,
                "appliedChanges": _describe_update_changes(
                    removed=removed_count,
                    added=added_count,
                    note_action=None,
                ),
                "updatedOrderTotal": 0,
                "error": f"failed to add modifier {modifier_id!r}: {exc}",
            }

    note_action: str | None = None
    if note_changed:
        note_action = "cleared note" if note_value is None else "updated note"
        print(f"[updateItemInOrder] updating note to {note_value!r}")
        try:
            await update_clover_line_item(
                creds["token"],
                creds["merchant_id"],
                creds["base_url"],
                order_id,
                matched_line_item_id,
                note=note_value,
            )
        except Exception as exc:
            print(f"[updateItemInOrder] update note failed: {exc!r}")
            return {
                "success": False,
                "itemName": matched_name,
                "appliedChanges": _describe_update_changes(
                    removed=removed_count,
                    added=added_count,
                    note_action=None,
                ),
                "updatedOrderTotal": 0,
                "error": f"failed to update line item note: {exc}",
            }

    updated_total = 0
    try:
        updated_order = await _get_order_data(session_id, creds, force_refresh=True)
        updated_total = updated_order.get("total", 0) or 0
        print(f"[updateItemInOrder] updated order total: {updated_total} cents")
    except Exception as exc:
        print(f"[updateItemInOrder] fetch order data failed: {exc!r}")

    result = {
        "success": True,
        "itemName": matched_name,
        "appliedChanges": _describe_update_changes(
            removed=removed_count,
            added=added_count,
            note_action=note_action,
        ),
        "updatedOrderTotal": updated_total,
        "error": None,
    }
    print(f"[updateItemInOrder] result: {result}")
    return result


async def calcOrderPrice(session_id: str, creds: dict | None = None) -> dict:
    """Return the current Clover-backed price breakdown for the session order.

    Use this before confirming an order or when the execution agent needs an
    authoritative subtotal / tax / total for the customer's current cart.

    Returns a dict:

        success (bool)
            True when pricing was calculated successfully.

        lineItems (list[dict])
            Current line-item pricing breakdown, including modifier prices.

        subtotal (int)
            Current order subtotal in cents.

        tax (int)
            Current order tax in cents.

        total (int)
            Current order total in cents.

        currency (str)
            Currency code, defaulting to ``USD``.

        error (str | None)
            Human-readable error message when ``success`` is False.

    Decision guide for the agent:
        - ``success`` True → use the totals as the source of truth for confirmation or price replies.
        - ``success`` False → tell the customer pricing could not be calculated right now.
    """
    print(f"[calcOrderPrice] session_id={session_id!r}")

    order_id = await cache_get(_session_clover_order_redis_key(session_id))
    if not order_id:
        result = {
            "success": True,
            "lineItems": [],
            "subtotal": 0,
            "tax": 0,
            "total": 0,
            "currency": "USD",
            "error": None,
        }
        print(f"[calcOrderPrice] no cached order id; result={result}")
        return result

    try:
        if creds is None:
            raise ValueError("creds must be provided")
        print(f"[calcOrderPrice] order_id={order_id!r}")

        order_data = await _get_order_data(session_id, creds)
        print(f"[calcOrderPrice] raw order_data keys: {list(order_data.keys())}")

        breakdown = _pricing_breakdown_from_order(order_data)
        result = {
            "success": True,
            "lineItems": breakdown["lineItems"],
            "subtotal": breakdown["subtotal"],
            "tax": breakdown["tax"],
            "total": breakdown["total"],
            "currency": breakdown["currency"],
            "error": None,
        }
        print(f"[calcOrderPrice] result={result}")
        return result
    except Exception as exc:
        print(f"[calcOrderPrice] error: {exc!r}")
        return {
            "success": False,
            "lineItems": [],
            "subtotal": 0,
            "tax": 0,
            "total": 0,
            "currency": "USD",
            "error": str(exc),
        }


async def confirmOrder(session_id: str, creds: dict | None = None) -> dict:
    """Submit the current Clover order and mark the chat session as confirmed."""
    print(f"[confirmOrder] session_id={session_id!r}")

    order_id = await cache_get(_session_clover_order_redis_key(session_id))
    if not order_id:
        result = {
            "success": False,
            "orderId": "",
            "confirmedItems": [],
            "finalTotal": 0,
            "estimatedPickuptime": None,
            "error": "order is empty",
        }
        print(f"[confirmOrder] no cached order id; result={result}")
        return result

    try:
        if creds is None:
            raise ValueError("creds must be provided")
        print(f"[confirmOrder] order_id={order_id!r}")

        current_order = await fetch_clover_order(
            creds["token"], creds["merchant_id"], creds["base_url"], order_id
        )
        current_line_items = _normalize_order_line_items(current_order)
        print(
            f"[confirmOrder] current line items: {[li.get('id') for li in current_line_items]}"
        )

        if not current_line_items:
            result = {
                "success": False,
                "orderId": order_id,
                "confirmedItems": [],
                "finalTotal": 0,
                "estimatedPickuptime": None,
                "error": "order is empty",
            }
            print(f"[confirmOrder] empty order; result={result}")
            return result

        await update_clover_order(
            creds["token"],
            creds["merchant_id"],
            creds["base_url"],
            order_id,
            state="Open",
        )

        final_order = await fetch_clover_order(
            creds["token"], creds["merchant_id"], creds["base_url"], order_id
        )
        final_raw_line_items = _normalize_order_line_items(final_order)
        breakdown = _pricing_breakdown_from_order(final_order)
        confirmed_items = [
            {
                "lineItemId": line_item["lineItemId"],
                "name": line_item["name"],
                "quantity": line_item["quantity"],
                # Clover often renders base item price separately from modifier rows on tickets.
                "price": final_raw_line_items[idx].get("price", 0) or 0,
                "lineTotal": line_item["lineTotal"],
            }
            for idx, line_item in enumerate(breakdown["lineItems"])
        ]

        await cache_set(_session_status_redis_key(session_id), "confirmed")

        result = {
            "success": True,
            "orderId": order_id,
            "confirmedItems": confirmed_items,
            # Minutes until the order is ready for pickup. Populated so the
            # agent can phrase the confirmation as "pickup in X minutes"
            # instead of a hallucinated clock time.
            "estimatedPickuptime": _DEFAULT_PICKUP_MINUTES,
            "error": None,
        }
        print(f"[confirmOrder] result={result}")
        return result
    except Exception as exc:
        print(f"[confirmOrder] error: {exc!r}")
        return {
            "success": False,
            "orderId": order_id,
            "confirmedItems": [],
            "finalTotal": 0,
            "estimatedPickuptime": None,
            "error": str(exc),
        }


async def cancelOrder(session_id: str, creds: dict | None = None) -> dict:
    """Cancel an unconfirmed Clover order and clear session order state."""
    print(f"[cancelOrder] session_id={session_id!r}")

    session_status = await cache_get(_session_status_redis_key(session_id))
    order_id = await cache_get(_session_clover_order_redis_key(session_id))
    print(f"[cancelOrder] session_status={session_status!r} order_id={order_id!r}")

    if session_status == "confirmed":
        result = {
            "success": False,
            "cancelledOrderId": order_id or None,
            "hadItems": False,
            "error": "order already confirmed and submitted",
        }
        print(f"[cancelOrder] confirmed session; result={result}")
        return result

    if not order_id:
        result = {
            "success": True,
            "cancelledOrderId": None,
            "hadItems": False,
            "error": None,
        }
        print(f"[cancelOrder] no order id; result={result}")
        return result

    try:
        if creds is None:
            raise ValueError("creds must be provided")

        had_items = False
        try:
            order_data = await fetch_clover_order(
                creds["token"], creds["merchant_id"], creds["base_url"], order_id
            )
            had_items = bool(_normalize_order_line_items(order_data))
            print(f"[cancelOrder] had_items={had_items}")
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code != 404:
                raise
            print(
                "[cancelOrder] pre-delete fetch returned 404; continuing with had_items=False"
            )

        try:
            await delete_clover_order(
                creds["token"], creds["merchant_id"], creds["base_url"], order_id
            )
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code != 404:
                raise
            print("[cancelOrder] delete returned 404; treating as already gone")

        await cache_delete(_session_order_state_redis_key(session_id))
        await cache_delete(_session_clover_order_redis_key(session_id))
        await _invalidate_order_data_cache(session_id)
        await cache_set(_session_status_redis_key(session_id), "cancelled")

        result = {
            "success": True,
            "cancelledOrderId": order_id,
            "hadItems": had_items,
            "error": None,
        }
        print(f"[cancelOrder] result={result}")
        return result
    except Exception as exc:
        print(f"[cancelOrder] error: {exc!r}")
        return {
            "success": False,
            "cancelledOrderId": order_id,
            "hadItems": False,
            "error": str(exc),
        }


async def getOrderLineItems(session_id: str, creds: dict | None = None) -> dict:
    """Return all line items currently in the customer's cart without modifying the order.

    Use this tool when you need to inspect what is in the order before acting on it —
    for example, before replacing an item, confirming an order, or answering a customer
    question about their cart contents.

    Parameters
    ----------
    session_id : str
        The chat session id. Pass the exact value supplied by the session context;
        do not normalise or transform it.

    Returns
    -------
    dict with keys:
        success (bool)     — True when the order was fetched successfully.
        orderId (str)      — Clover order id for this session.
        lineItems (list)   — One dict per line item in the order:
                               lineItemId (str)  — Clover line item id.
                               name (str)        — Display name of the item.
                               quantity (int)    — Number of units (unitQty / 1000, min 1).
                               price (int)       — Line-level total in cents from Clover.
                             Empty list when the cart has no items.
        orderTotal (int)   — Current order total in cents from Clover.
        error (str | None) — Human-readable error message, or None on success.

    Decision guide
    --------------
    - success True  → use lineItems to inform the next action or answer the customer.
    - success False → surface error to the customer and do not proceed with order mutations.
    """
    print(f"[getOrderLineItems] session_id={session_id!r}")
    try:
        if creds is None:
            raise ValueError("creds must be provided")
        order_data = await _get_order_data(session_id, creds)
        order_id = order_data.get("id", "")
        print(f"[getOrderLineItems] order_id={order_id!r}")
        print(f"[getOrderLineItems] raw order_data keys: {list(order_data.keys())}")

        raw_list = _normalize_order_line_items(order_data)

        line_items = [
            {
                "lineItemId": li.get("id", ""),
                "name": li.get("name", ""),
                "quantity": _line_item_quantity(li),
                "price": li.get("price", 0),
            }
            for li in raw_list
        ]

        order_total: int = order_data.get("total", 0) or 0
        print(f"[getOrderLineItems] line_items={line_items}, orderTotal={order_total}")
        return {
            "success": True,
            "orderId": order_id,
            "lineItems": line_items,
            "orderTotal": order_total,
            "error": None,
        }
    except Exception as exc:
        print(f"[getOrderLineItems] error: {exc!r}")
        return {
            "success": False,
            "orderId": "",
            "lineItems": [],
            "orderTotal": 0,
            "error": str(exc),
        }


async def getPreviousKMessages(session_id: str, k: int | None = None) -> dict:
    """Return the most recent stored session messages from Redis in chronological order.

    Reads list entries from ``message:{session_id}``, where each entry is expected
    to be a JSON object with ``role``, ``content``, and ``timestamp`` fields.
    Stored ``user``/``assistant`` roles are normalized to ``customer``/``agent``.
    Unsupported roles such as ``system`` are ignored in the returned message list.
    """
    print(f"[getPreviousKMessages] session_id={session_id!r} k={k!r}")
    try:
        resolved_k = settings.DEFAULT_PREVIOUS_MESSAGES_K if k is None else k
        if resolved_k < -1:
            raise ValueError("k must be -1 or a non-negative integer")

        redis_key = _session_messages_redis_key(session_id)
        total_message_count = await cache_list_length(redis_key)
        if total_message_count <= 0:
            return {
                "success": True,
                "messages": [],
                "totalMessageCount": 0,
                "hasEarlierHistory": False,
                "error": None,
            }

        if resolved_k == -1:
            raw_messages = await cache_list_range(redis_key, 0, -1)
        elif resolved_k == 0:
            raw_messages = []
        else:
            raw_messages = await cache_list_range(redis_key, -resolved_k, -1)

        messages: list[dict] = []
        for raw_message in raw_messages:
            normalized = _normalize_session_history_message(raw_message)
            if normalized is not None:
                messages.append(normalized)

        fetched_window_size = len(raw_messages)
        has_earlier_history = total_message_count > fetched_window_size
        print(
            "[getPreviousKMessages] "
            f"total_message_count={total_message_count} "
            f"returned_messages={len(messages)} "
            f"has_earlier_history={has_earlier_history}"
        )
        return {
            "success": True,
            "messages": messages,
            "totalMessageCount": total_message_count,
            "hasEarlierHistory": has_earlier_history,
            "error": None,
        }
    except Exception as exc:
        print(f"[getPreviousKMessages] error: {exc!r}")
        return {
            "success": False,
            "messages": [],
            "totalMessageCount": 0,
            "hasEarlierHistory": False,
            "error": str(exc),
        }


async def summarizeConversationHistory(session_id: str, k: int) -> dict:
    """Summarize all stored session messages before the last ``k`` raw Redis entries."""
    print(f"[summarizeConversationHistory] session_id={session_id!r} k={k!r}")
    try:
        if k < 0:
            raise ValueError("k must be a non-negative integer")

        redis_key = _session_messages_redis_key(session_id)
        total_message_count = await cache_list_length(redis_key)
        messages_covered = max(total_message_count - k, 0)
        if messages_covered == 0:
            return {
                "success": True,
                "summary": "",
                "messagesCovered": 0,
                "cachedAt": None,
                "error": None,
            }

        summary_cache_key = _session_history_summary_cache_key(
            session_id, messages_covered
        )
        cached_summary = await cache_get(summary_cache_key)
        if cached_summary:
            try:
                parsed_cached_summary = _parse_cached_history_summary(cached_summary)
                if parsed_cached_summary["messagesCovered"] == messages_covered:
                    return {
                        "success": True,
                        "summary": parsed_cached_summary["summary"],
                        "messagesCovered": parsed_cached_summary["messagesCovered"],
                        "cachedAt": parsed_cached_summary["cachedAt"],
                        "error": None,
                    }
            except ValueError as exc:
                print(
                    "[summarizeConversationHistory] "
                    f"ignoring invalid cached summary: {exc!r}"
                )

        raw_messages = await cache_list_range(redis_key, 0, -1)
        raw_history = raw_messages[:-k] if k > 0 else raw_messages

        history_to_summarize: list[dict] = []
        for raw_message in raw_history:
            normalized = _normalize_session_history_message(raw_message)
            if normalized is not None:
                history_to_summarize.append(normalized)

        summary = await _summarize_session_history(history_to_summarize)
        cached_at = datetime.now(timezone.utc).isoformat()
        await cache_set(
            summary_cache_key,
            _serialize_cached_history_summary(
                summary=summary,
                messages_covered=messages_covered,
                cached_at=cached_at,
            ),
            ttl=_SESSION_CLOVER_ORDER_REDIS_TTL_SECONDS,
        )
        return {
            "success": True,
            "summary": summary,
            "messagesCovered": messages_covered,
            "cachedAt": cached_at,
            "error": None,
        }
    except Exception as exc:
        print(f"[summarizeConversationHistory] error: {exc!r}")
        return {
            "success": False,
            "summary": "",
            "messagesCovered": 0,
            "cachedAt": None,
            "error": str(exc),
        }

async def _summarize_session_history(history: list[dict]) -> str:
    if not history:
        return ""
    return await generate_text(
        _summary_prompt_messages(history),
        temperature=0,
        max_output_tokens=_SUMMARIZE_HISTORY_MAX_OUTPUT_TOKENS,
    )


async def get_order_id_for_session(session_id: str, creds: dict) -> str:
    """Look up the active Clover order id stored for this chat session in Redis.

    Creates a new empty Clover order (and caches its id) when none exists yet.
    """
    redis_key = _session_clover_order_redis_key(session_id)
    order_id = await cache_get(redis_key)
    if not order_id:
        response = await create_clover_empty_order(
            creds["token"], creds["merchant_id"], creds["base_url"]
        )
        order_id = response.get("id")
        if not order_id:
            raise ValueError("Failed to create empty order")
        await cache_set(
            redis_key, order_id, ttl=_SESSION_CLOVER_ORDER_REDIS_TTL_SECONDS
        )
    return order_id

async def getMenuLink(session_id: str, merchant_id: str, creds: dict | None = None) -> dict:
    """Return a shareable menu URL for the merchant.

    Call this when the customer asks to see the full menu. Returns a URL they can open
    in their browser to browse all available items.

    Args:
        session_id: The chat session identifier. Used for logging/context.
        merchant_id: The Clover merchant id for this restaurant.
        creds: Clover credentials dict (may contain a ``menu_url`` key). Pass None
            if credentials are not available — an error will be returned.

    Returns a dict:
        success (bool)
            True when a menu URL was found.
        menu_url (str | None)
            The shareable URL, or None when not configured.
        error (str | None)
            Human-readable reason for failure, or None on success.

    Decision guide for the agent:
        - ``success`` True with ``menu_url`` → send the URL to the customer.
        - ``success`` False → inform customer that a menu link is not available.
    """
    menu_url = "https://www.smashnwings.com/menu"
    print(f"[getMenuLink] session_id={session_id!r} merchant_id={merchant_id!r} menu_url={menu_url!r}")
    return {"success": True, "menu_url": menu_url, "error": None}


async def getItemsNotAvailableToday(merchant_id: str, creds: dict | None = None) -> dict:
    """Return a list of menu items that are currently unavailable.

    Call this when the customer asks what is off today or what items cannot be ordered.
    Scans the cached menu and returns items where ``available`` is False.

    Args:
        merchant_id: The Clover merchant id used to look up the menu cache.
        creds: Clover credentials dict required to fetch a fresh menu if the cache
            is stale. Pass None if credentials are not available.

    Returns a dict:
        success (bool)
            True when the menu was loaded successfully.
        unavailable_items (list[dict])
            Each entry: ``{"id": str, "name": str}``. Empty list when all items are available.
        error (str | None)
            Human-readable reason for failure, or None on success.

    Decision guide for the agent:
        - ``success`` True, empty ``unavailable_items`` → tell customer everything is available.
        - ``success`` True, non-empty list → read out the unavailable item names.
        - ``success`` False → inform customer you couldn't load menu availability.
    """
    print(f"[getItemsNotAvailableToday] merchant_id={merchant_id!r}")
    if creds is None:
        print("[getItemsNotAvailableToday] no creds available")
        return {"success": False, "unavailable_items": [], "error": "Credentials unavailable."}

    try:
        menu_items = await _menu_items_cached_or_fresh(creds)
    except Exception as exc:
        print(f"[getItemsNotAvailableToday] failed to load menu: {exc!r}")
        return {"success": False, "unavailable_items": [], "error": str(exc)}

    unavailable: list[dict] = []
    for item_id, item in menu_items.get("by_id", {}).items():
        if not item.get("available", True):
            unavailable.append({"id": str(item_id), "name": str(item.get("name", ""))})

    print(f"[getItemsNotAvailableToday] found {len(unavailable)} unavailable items")
    return {"success": True, "unavailable_items": unavailable, "error": None}


async def humanInterventionNeeded(session_id: str, escalation_type: str, merchant_id: str) -> dict:
    """Flag a session for human review by calling the escalation webhook.

    Call this when the customer's intent is ``escalation`` or when the situation
    cannot be resolved automatically (e.g., repeated failures, complaints, or
    requests outside system capability).F

    Args:
        session_id: The chat session identifier.
        escalation_type: Category of escalation. Must be one of:
            "order_cancellation" — customer wants to cancel their order.
            "made_changes_to_order" — customer made or requested changes after confirmation.
            "asking_for_pickup_time" — customer is asking about pickup time.
            "questions_about_their_order" — customer has questions about their order.
            "post_confirm_request" — customer made a request after the order was already confirmed.
        merchant_id: The merchant identifier associated with this session.

    Returns a dict:
        success (bool)
            True when the escalation endpoint returned a 2xx response.
        escalated (bool)
            True when the request was sent and accepted.
        error (str | None)
            Human-readable reason for failure, or None on success.

    Decision guide for the agent:
        - ``success`` True → tell the customer a team member will follow up.
        - ``success`` False → still inform the customer and advise them to call the store.
    """
    print(f"[humanInterventionNeeded] session_id={session_id!r} escalation_type={escalation_type!r} merchant_id={merchant_id!r}")
    timestamp = datetime.now(timezone.utc).isoformat()
    payload = {"order_id": session_id, "escalation_type": escalation_type, "timestamp": timestamp, "user_id": merchant_id}

    escalation_url = settings.ESCALATION_URL + "/api/escalate"
    if not escalation_url:
        print("[humanInterventionNeeded] ESCALATION_URL not configured")
        return {"success": False, "escalated": False, "error": "ESCALATION_URL is not configured"}

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(escalation_url, json=payload)
            response.raise_for_status()
        print(f"[humanInterventionNeeded] escalation sent status={response.status_code}")
        return {"success": True, "escalated": True, "error": None}
    except Exception as exc:
        print(f"[humanInterventionNeeded] failed: {exc!r}")
        return {"success": False, "escalated": False, "error": str(exc)}


async def suggestedPickupTime(session_id: str, pickup_time_minutes: int, firebase_uid: str) -> dict:
    """Notify the external system that the customer has suggested a pickup time.

    Call this ONLY when the customer explicitly states a pickup time (e.g.,
    "I'll be there in 20 minutes", "can I pick this up in an hour?").
    Do NOT call it when the customer has not mentioned a time, or when you
    are simply confirming or recapping an order.

    Args:
        session_id: The chat session identifier.
        pickup_time_minutes: The pickup time suggested by the customer, expressed
            as a whole number of minutes from now (e.g., 30 for "in 30 minutes",
            60 for "in an hour"). Convert the customer's natural-language phrase
            to minutes before passing — do NOT pass a raw string.
        firebase_uid: The Firebase UID (original_merchant_id) for this merchant.
            This is sent as user_id in the webhook payload. Do NOT pass the Clover merchant ID.

    Returns a dict:
        success (bool)
            True when the webhook returned a 2xx response.
        pickup_time_minutes (int)
            Echo of the ``pickup_time_minutes`` argument.
        timestamp (str)
            ISO-8601 UTC timestamp of when this tool was called.
        error (str | None)
            Human-readable reason for failure, or None on success.

    Decision guide for the agent:
        - ``success`` True  → acknowledge the pickup time to the customer
          (e.g., "Got it, we'll have your order ready in ~30 minutes!").
        - ``success`` False → still acknowledge the time to the customer but
          note internally that the notification could not be sent.
    """
    print(f"[suggestedPickupTime] session_id={session_id!r} pickup_time_minutes={pickup_time_minutes!r} firebase_uid={firebase_uid!r}")
    timestamp = datetime.now(timezone.utc).isoformat()
    payload = {
        "order_id": session_id,
        "pickup_time_suggestion": pickup_time_minutes,
        "pickup_time_suggestion_timestamp": timestamp,
        "user_id": firebase_uid,
    }

    pickup_url = settings.ESCALATION_URL + "/api/suggested-pickup-time"
    if not settings.ESCALATION_URL:
        print("[suggestedPickupTime] ESCALATION_URL not configured")
        return {"success": False, "pickup_time_minutes": pickup_time_minutes, "timestamp": timestamp, "error": "ESCALATION_URL is not configured"}

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(pickup_url, json=payload)
            response.raise_for_status()
        print(f"[suggestedPickupTime] webhook sent status={response.status_code}")
        return {"success": True, "pickup_time_minutes": pickup_time_minutes, "timestamp": timestamp, "error": None}
    except Exception as exc:
        print(f"[suggestedPickupTime] failed: {exc!r}")
        return {"success": False, "pickup_time_minutes": pickup_time_minutes, "timestamp": timestamp, "error": str(exc)}


async def askingForPickupTime(session_id: str, firebase_uid: str) -> dict:
    """Notify the restaurant that the customer is asking about or wants to know their pickup time.

    Call this in two situations:
      1. When the customer asks about pickup time (e.g., "how long will my order take?",
         "when will it be ready?", "what's my wait time?").
      2. Always alongside confirmOrder — every order confirmation should trigger this ping.

    Do NOT call this when the customer is SUGGESTING a pickup time (e.g., "I'll be there in
    30 minutes") — use suggestedPickupTime for that case.

    Args:
        session_id: The chat session identifier.
        firebase_uid: The Firebase UID (original_merchant_id) for this merchant.
            This is sent as user_id in the webhook payload. Do NOT pass the Clover merchant ID.

    Returns a dict:
        success (bool)
            True when the webhook returned a 2xx response.
        error (str | None)
            Human-readable reason for failure, or None on success.

    Decision guide for the agent:
        - success True  → no action needed; proceed normally.
        - success False → no action needed; proceed normally (silent best-effort ping).
    """
    print(f"[askingForPickupTime] session_id={session_id!r} firebase_uid={firebase_uid!r}")
    payload = {"order_id": session_id, "user_id": firebase_uid}

    if not settings.ESCALATION_URL:
        print("[askingForPickupTime] ESCALATION_URL not configured")
        return {"success": False, "error": "ESCALATION_URL is not configured"}

    ping_url = settings.ESCALATION_URL + "/api/ping-for-pickup"

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(ping_url, json=payload)
            response.raise_for_status()
        print(f"[askingForPickupTime] webhook sent status={response.status_code}")
        return {"success": True, "error": None}
    except Exception as exc:
        print(f"[askingForPickupTime] failed: {exc!r}")
        return {"success": False, "error": str(exc)}


async def getPreviousOrdersDetails(session_id: str, limit: int = 3) -> dict:
    """Retrieve stored order history for a session from Redis.

    Call this when the customer asks about their past orders or wants to reorder.
    Reads order history entries stored under the session's order history key.

    Args:
        session_id: The chat session identifier used to look up order history.
        limit: Maximum number of past orders to return. Defaults to 3.
            Pass a larger number only when the customer explicitly asks for more history.

    Returns a dict:
        success (bool)
            True when history was read (even if empty).
        orders (list[dict])
            Each entry: ``{"order_id": str, "items": list, "total": int, "timestamp": str}``.
            Empty list when no history exists.
        error (str | None)
            Human-readable reason for failure, or None on success.

    Decision guide for the agent:
        - ``success`` True, non-empty ``orders`` → summarize the most recent orders for the customer.
        - ``success`` True, empty ``orders`` → tell customer no previous orders were found.
        - ``success`` False → inform customer you couldn't load order history.
    """
    print(f"[getPreviousOrdersDetails] session_id={session_id!r} limit={limit!r}")
    history_key = f"order_history:{session_id}"
    try:
        safe_limit = max(1, int(limit or 3))
        raw_entries = await cache_list_range(history_key, 0, safe_limit - 1)
        orders: list[dict] = []
        for entry in raw_entries:
            try:
                orders.append(json.loads(entry))
            except Exception:
                pass
        print(f"[getPreviousOrdersDetails] found {len(orders)} orders")
        return {"success": True, "orders": orders, "error": None}
    except Exception as exc:
        print(f"[getPreviousOrdersDetails] failed: {exc!r}")
        return {"success": False, "orders": [], "error": str(exc)}


