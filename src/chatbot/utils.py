# Helper functions for chatbot
from src.chatbot.constants import ConversationState, _MENU_AVAILABILITY_STALE_SECONDS, _HARDCODED_SALES_TAX_PERCENT
from src.chatbot.constants import _MENU_CACHE_VERSION, _MENU_CACHE_TTL_SECONDS, _MENU_ITEM_ID_BLOCKLIST
from src.chatbot.promptsv2 import _SUMMARIZE_HISTORY_SYSTEM_PROMPT
from src.cache import cache_get, cache_set
import json
import re
import time
from src.menu.loader import build_normalized_items


def _parse_safely(value: str | None, enum_cls):
    if not value:
        return None
    try:
        return enum_cls(value.strip().lower())
    except ValueError:
        return None

def _parse_conversation_state(value: str | None) -> ConversationState | None:
    if not value:
        return None
    try:
        return ConversationState(value.strip().lower())
    except ValueError:
        return None

def _clover_creds_redis_key(merchant_id: str) -> str:
    return f"clover_creds:{merchant_id}"


def _menu_cache_key(merchant_id: str) -> str:
    return f"menu:v{_MENU_CACHE_VERSION}:{merchant_id}"


def _menu_fetched_at_key(merchant_id: str) -> str:
    return f"menu:v{_MENU_CACHE_VERSION}:fetched_at:{merchant_id}"


def _session_clarification_and_intent_redis_key(session_id: str) -> str:
    return f"clarification_and_intent:{session_id}"


def _session_intent_queue_redis_key(session_id: str) -> str:
    return f"intent_queue:{session_id}"


def _session_ordering_stage_redis_key(session_id: str) -> str:
    return f"ordering_stage:{session_id}"


async def get_ordering_stage(session_id: str) -> str:
    """Return the current ordering stage for a session.

    Possible values: 'ordering' (default), 'awaiting_anything_else',
    'awaiting_order_confirm'.
    """
    key = _session_ordering_stage_redis_key(session_id)
    raw = await cache_get(key)
    return raw or "ordering"


async def set_ordering_stage(session_id: str, stage: str) -> None:
    """Persist the ordering stage for a session."""
    from src.chatbot.constants import _SESSION_CLARIFICATION_AND_INTENT_TTL_SECONDS
    key = _session_ordering_stage_redis_key(session_id)
    await cache_set(key, stage, ttl=_SESSION_CLARIFICATION_AND_INTENT_TTL_SECONDS)
    print(f"[set_ordering_stage] session_id={session_id!r} stage={stage!r}")


def _session_clover_order_redis_key(session_id: str) -> str:
    """Build the Redis key used to store the Clover order id for a chat session.

    This returns the key string only. Read the order id with ``cache_get(key)``
    and write it with ``cache_set(key, order_id, ...)``.
    """
    return f"order:session:{session_id}"


def _session_status_redis_key(session_id: str) -> str:
    return f"session:{session_id}:status"


def _session_order_state_redis_key(session_id: str) -> str:
    return f"orderstate:{session_id}"


def _session_messages_redis_key(session_id: str) -> str:
    return f"message:{session_id}"


def _session_history_summary_cache_key(session_id: str, messages_covered: int) -> str:
    return f"summary:{session_id}:{messages_covered}"


def _buffer_messages_redis_key(session_id: str) -> str:
    return f"buffer:messages:{session_id}"


def _buffer_timer_redis_key(session_id: str) -> str:
    return f"buffer:timer:{session_id}"


def _buffer_result_redis_key(session_id: str) -> str:
    return f"buffer:result:{session_id}"


def _buffer_lock_redis_key(session_id: str) -> str:
    return f"buffer:lock:{session_id}"

def _normalize_session_history_message(raw_message: str) -> dict | None:
    payload = json.loads(raw_message)
    if not isinstance(payload, dict):
        raise ValueError("Session history entry must be a JSON object")

    role_raw = payload.get("role")
    content = payload.get("content")
    timestamp = payload.get("timestamp")

    if not isinstance(role_raw, str):
        raise ValueError("Session history entry role must be a string")
    if not isinstance(content, str):
        raise ValueError("Session history entry content must be a string")
    if not isinstance(timestamp, str):
        raise ValueError("Session history entry timestamp must be a string")

    role = {
        "user": "customer",
        "assistant": "agent",
        "customer": "customer",
        "agent": "agent",
    }.get(role_raw)
    if role is None:
        return None

    return {
        "role": role,
        "content": content,
        "timestamp": timestamp,
    }

def _parse_cached_history_summary(raw_summary: str) -> dict:
    payload = json.loads(raw_summary)
    if not isinstance(payload, dict):
        raise ValueError("Cached summary entry must be a JSON object")

    summary = payload.get("summary")
    messages_covered = payload.get("messagesCovered")
    cached_at = payload.get("cachedAt")

    if not isinstance(summary, str):
        raise ValueError("Cached summary entry summary must be a string")
    if not isinstance(messages_covered, int):
        raise ValueError("Cached summary entry messagesCovered must be an integer")
    if not isinstance(cached_at, str):
        raise ValueError("Cached summary entry cachedAt must be a string")

    return {
        "summary": summary,
        "messagesCovered": messages_covered,
        "cachedAt": cached_at,
    }

def _serialize_cached_history_summary(
    *,
    summary: str,
    messages_covered: int,
    cached_at: str,
) -> str:
    return json.dumps(
        {
            "summary": summary,
            "messagesCovered": messages_covered,
            "cachedAt": cached_at,
        }
    )

def _summary_prompt_messages(history: list[dict]) -> list[dict[str, str]]:
    llm_history: list[dict[str, str]] = []
    for message in history:
        role = message["role"]
        if role == "customer":
            llm_role = "user"
        elif role == "agent":
            llm_role = "assistant"
        else:
            continue
        llm_history.append(
            {
                "role": llm_role,
                "content": message["content"].replace("\x00", ""),
            }
        )

    return [
        {"role": "system", "content": _SUMMARIZE_HISTORY_SYSTEM_PROMPT},
        *llm_history,
        {
            "role": "user",
            "content": (
                "Summarize the earlier conversation above in one short factual paragraph."
            ),
        },
    ]


def _collect_modifier_ids_from_item(item_row: dict) -> set[str]:
    """Return all modifier IDs reachable from a single item row.

    Handles two formats:
    - Raw Clover:      item["modifierGroups"]["elements"][group]["modifiers"]["elements"][mod]["id"]
    - Normalised list: item["modifier_groups"][group]["modifiers"][mod]["id"]
    """
    ids: set[str] = set()

    for group in item_row.get("modifierGroups", {}).get("elements", []):
        for mod in group.get("modifiers", {}).get("elements", []):
            mod_id = mod.get("id")
            if mod_id:
                ids.add(mod_id)

    for group in item_row.get("modifier_groups", []):
        for mod in group.get("modifiers", []):
            mod_id = mod.get("id")
            if mod_id:
                ids.add(mod_id)

    return ids


def _merge_numeric_name_variants(norm_name: str, items: list[dict]) -> list[dict]:
    """Collapse numeric variants into one item with a synthetic 'Quantity' modifier group.

    Splits the group into items that have a numeric token in their original name
    and those that don't. If 2+ numeric variants exist, merges only those and
    returns a single merged item (non-numeric placeholders are dropped from
    by_name but remain accessible in by_id). Returns the original list unchanged
    when fewer than 2 numeric variants are found.
    """
    if len(items) <= 1:
        return items

    numeric_items: list[dict] = []
    quantity_options: list[dict] = []

    for item in items:
        orig = item.get("_original_name", "")
        m = re.search(r'\b(\d+)\b', orig)
        if m is not None:
            numeric_items.append(item)
            quantity_options.append({
                "id": item["id"],
                "name": m.group(1),
                "price": item.get("price", 0) or 0,
            })

    if len(numeric_items) < 2:
        return items  # not enough numeric variants — skip merging

    quantity_options.sort(key=lambda x: int(x["name"]))

    base = dict(numeric_items[0])
    base["modifier_groups"] = list(base.get("modifier_groups") or []) + [{
        "id": f"quantity__{norm_name}",
        "name": "Quantity",
        "min_required": 1,
        "max_allowed": 1,
        "modifiers": quantity_options,
    }]
    base["merged"] = True
    base.pop("_original_name", None)
    return [base]


# def _normalize_item_name(name: str) -> str | None:
#     """Return a cleaned name for menu indexing, or None to skip the item.
#
#     Rules applied in order:
#     - Skip combo names that contain '&' (e.g. "Sandos & Fries", "Tenders & Fries").
#     - Skip items whose raw name is exactly "wings" (case-insensitive) — Clover placeholder with price 0.
#     - Remove standalone numeric tokens (e.g. "5", "10").
#     - Remove the standalone word "pc" (case-insensitive).
#     - Collapse extra whitespace.
#     Returns None when the result is empty or the item should be excluded.
#     """
#     if _COMBO_NAME_RE.search(name):
#         return None
#     if name.strip().lower() == "wings":
#         return None
#     cleaned = re.sub(r'\b\d+\b', '', name)
#     cleaned = re.sub(r'\bpc\b', '', cleaned, flags=re.IGNORECASE)
#     cleaned = ' '.join(cleaned.split())
#     return cleaned if cleaned else None


async def _normalize_menu(raw: dict) -> dict:
    """Normalize raw Clover menu data into multiple fast-access indexes.

    Returns:
        {
            "by_id": {id: item},
            "by_name": {lower_name: [items]},
            "by_category": {category_name: [items]},
            "by_modifier_id": {modifier_id: item_id}
        }
    """
    by_id: dict = {}
    by_name: dict = {}
    by_category: dict = {}
    by_modifier_id: dict = {}

    for item in build_normalized_items(raw):
        if item.get("deleted"):
            continue

        item_id = item["id"]
        if item_id in _MENU_ITEM_ID_BLOCKLIST:
            continue

        raw_name = item.get("name", "").strip()
        if not raw_name:
            continue
        by_id[item_id] = item

        by_name.setdefault(raw_name.lower(), []).append(item)

        category_name = str(item.get("category_name", "")).strip()
        if category_name:
            by_category.setdefault(category_name, []).append(item)

        for mod_id in _collect_modifier_ids_from_item(item):
            by_modifier_id[mod_id] = item_id

    for norm_name_key in list(by_name.keys()):
        if len(by_name[norm_name_key]) > 1:
            by_name[norm_name_key] = _merge_numeric_name_variants(
                norm_name_key, by_name[norm_name_key]
            )

    for item in by_id.values():
        item.pop("_original_name", None)

    return {
        "by_id": by_id,
        "by_name": by_name,
        "by_category": by_category,
        "by_modifier_id": by_modifier_id,
    }

async def _persist_menu_items_cache(merchant_id: str, items_by_name: dict) -> None:
    await cache_set(_menu_cache_key(merchant_id), json.dumps(items_by_name), ttl=_MENU_CACHE_TTL_SECONDS)
    await cache_set(_menu_fetched_at_key(merchant_id), str(int(time.time())), ttl=_MENU_CACHE_TTL_SECONDS)


async def _menu_cache_age_seconds(merchant_id: str) -> float | None:
    raw = await cache_get(_menu_fetched_at_key(merchant_id))
    if not raw:
        return None
    try:
        ts = int(raw)
    except ValueError:
        return None
    return max(0.0, time.time() - ts)


def _menu_snapshot_considered_fresh(age_seconds: float | None) -> bool:
    if age_seconds is None:
        return False
    return age_seconds < _MENU_AVAILABILITY_STALE_SECONDS

def _availability_result(
    *,
    available: bool,
    item_id: str,
    item_name: str,
    unavailable_reason: str | None,
) -> dict:
    return {
        "Available": available,
        "itemId": item_id,
        "itemName": item_name,
        "unavailableReason": unavailable_reason,
    }


def _item_not_found_result(item_id: str) -> dict:
    return _availability_result(
        available=False,
        item_id=item_id,
        item_name="",
        unavailable_reason="item not found on menu",
    )


def _normalize_order_line_items(order_data: dict) -> list[dict]:
    raw_line_items = order_data.get("lineItems") or []
    if isinstance(raw_line_items, dict):
        return raw_line_items.get("elements", [])
    if isinstance(raw_line_items, list):
        return raw_line_items
    return []


def _line_item_quantity(line_item: dict) -> int:
    return max(1, (line_item.get("unitQty") or 1000) // 1000)


def _extract_line_item_modification_records(line_item: dict) -> list[dict]:
    records: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for key in ("modifications", "modifiers"):
        raw = line_item.get(key) or []
        if isinstance(raw, dict):
            rows = raw.get("elements", [])
        elif isinstance(raw, list):
            rows = raw
        else:
            rows = []

        for row in rows:
            modifier = row.get("modifier") or {}
            modifier_id = modifier.get("id") or row.get("modifierId")
            modification_id = row.get("id") or row.get("modificationId")
            if not modifier_id or not modification_id:
                continue
            fingerprint = (modification_id, modifier_id)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            records.append(
                {
                    "modification_id": modification_id,
                    "modifier_id": modifier_id,
                    "modifier_name": modifier.get("name") or row.get("name") or "",
                    "price": row.get("amount")
                    or modifier.get("price")
                    or row.get("price")
                    or 0,
                }
            )

    return records

def _describe_update_changes(
    *, removed: int, added: int, note_action: str | None
) -> str:
    parts: list[str] = []
    if removed:
        parts.append(
            f"removed {removed} modifier"
            if removed == 1
            else f"removed {removed} modifiers"
        )
    if added:
        parts.append(
            f"added {added} modifier" if added == 1 else f"added {added} modifiers"
        )
    if note_action:
        parts.append(note_action)
    if not parts:
        return "no changes applied"
    return ", ".join(parts)


def _priced_line_item(line_item: dict) -> dict:
    quantity = _line_item_quantity(line_item)
    modifier_prices = [
        {
            "modifierId": record["modifier_id"],
            "name": record["modifier_name"],
            "price": record["price"],
        }
        for record in _extract_line_item_modification_records(line_item)
    ]

    base_line_price = line_item.get("price") or 0
    explicit_unit_price = line_item.get("unitPrice")
    item_unit_price = (line_item.get("item") or {}).get("price")
    if explicit_unit_price is not None:
        unit_price = explicit_unit_price
    elif item_unit_price is not None:
        unit_price = item_unit_price
    elif quantity > 1 and base_line_price > 0 and base_line_price % quantity == 0:
        unit_price = base_line_price // quantity
    else:
        unit_price = base_line_price

    modifier_total = sum(modifier["price"] for modifier in modifier_prices)
    line_total = line_item.get("priceWithModifiers")
    if line_total is None:
        line_total = base_line_price + modifier_total

    return {
        "lineItemId": line_item.get("id", ""),
        "name": line_item.get("name", ""),
        "quantity": quantity,
        "unitPrice": unit_price,
        "modifierPrices": modifier_prices,
        "lineTotal": line_total,
    }


async def saveClarificationAndIntent(
    session_id: str,
    clarification_questions: list[str] | str,
    parsed_intents: list[dict],
    agent_questions: list[str] | None = None,
) -> None:
    """
    Persist the Execution Agent's clarification questions and the Parsing Agent's
    parsed intents to Redis under the given session_id.

    - session_id: the chat session identifier
    - clarification_questions: list of free-text questions from pending_clarifications
    - parsed_intents: list of dicts serialized from ParsedRequestsPayload.data
      (each has keys: intent, confidence_level, request_items, request_details)
    - agent_questions: sentences ending in '?' extracted from the agent reply (optional)

    TTL: 3 hours (_SESSION_CLARIFICATION_AND_INTENT_TTL_SECONDS)
    Overwrites any previously saved value for the session.
    """
    import datetime
    from src.chatbot.constants import _SESSION_CLARIFICATION_AND_INTENT_TTL_SECONDS

    payload = {
        "clarification_questions": clarification_questions,
        "parsed_intents": parsed_intents,
        "agent_questions": agent_questions or [],
        "saved_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    key = _session_clarification_and_intent_redis_key(session_id)
    await cache_set(key, json.dumps(payload), ttl=_SESSION_CLARIFICATION_AND_INTENT_TTL_SECONDS)
    print(
        "[saveClarificationAndIntent]",
        f"session_id={session_id!r}",
        f"clarification_count={len(clarification_questions)}",
        f"intent_count={len(parsed_intents)}",
        f"agent_questions_count={len(agent_questions or [])}",
    )


async def getClarificationAndIntent(session_id: str) -> dict:
    """
    Retrieve the most recently saved clarification questions and parsed intents
    for a session from Redis.

    Call this to read back what saveClarificationAndIntent last persisted.
    Returns None-equivalent data (empty lists) when nothing has been saved yet
    or the key has expired.

    Parameters:
    - session_id: the chat session identifier — same value passed to
      saveClarificationAndIntent when the data was written.

    Returns a dict with the following fields:
    - success (bool): True if data was found and parsed; False on missing key or error.
    - clarification_questions (list[str]): questions the agent needed answered.
      Empty list when success is False or no questions were pending.
    - parsed_intents (list[dict]): parsed intent items from the Parsing Agent.
      Each dict has keys: Intent, Confidence_level, Request_items, Request_details.
      Empty list when success is False or no intents were saved.
    - saved_at (str | None): ISO timestamp of when the data was written.
      None when success is False.
    - error (str | None): human-readable error message; None when success is True.

    Decision guide:
    - success True, clarification_questions non-empty → pending clarifications exist
    - success True, clarification_questions empty → no clarifications needed last turn
    - success False, error contains "not found" → key expired or never written
    - success False, other error → Redis or JSON parse failure
    """
    key = _session_clarification_and_intent_redis_key(session_id)
    try:
        raw = await cache_get(key)
        if raw is None:
            return {
                "success": False,
                "clarification_questions": [],
                "parsed_intents": [],
                "saved_at": None,
                "error": "clarification and intent not found for session",
            }
        payload = json.loads(raw)
        return {
            "success": True,
            "clarification_questions": payload.get("clarification_questions", []),
            "parsed_intents": payload.get("parsed_intents", []),
            "agent_questions": payload.get("agent_questions", []),
            "saved_at": payload.get("saved_at"),
            "error": None,
        }
    except Exception as exc:
        return {
            "success": False,
            "clarification_questions": [],
            "parsed_intents": [],
            "agent_questions": [],
            "saved_at": None,
            "error": str(exc),
        }


def extract_questions_from_reply(reply: str) -> list[str]:
    """Split reply on sentence boundaries and return sentences ending with '?'.

    Parameters:
    - reply: the full text of the agent's reply

    Returns a list of question sentences found in the reply.
    Returns an empty list if no questions are present.
    """
    import re
    sentences = re.split(r'(?<=[.!?])\s+', reply.strip())
    return [s.strip() for s in sentences if s.strip().endswith('?')]


async def get_intent_queue(session_id: str) -> list[dict]:
    """Load the intent queue for a session from Redis.

    Returns an empty list when the key does not exist or has expired.
    Each entry is a dict serialized from IntentQueueEntry.
    """
    key = _session_intent_queue_redis_key(session_id)
    raw = await cache_get(key)
    if not raw:
        return []
    try:
        return json.loads(raw)
    except Exception as exc:
        print(f"[get_intent_queue] failed to parse queue session_id={session_id!r} error={exc!r}")
        return []


async def save_intent_queue(session_id: str, queue: list[dict]) -> None:
    """Persist the intent queue to Redis.

    Entries with status 'done' should be removed before calling this.
    TTL matches the clarification-and-intent TTL (3 hours).
    """
    from src.chatbot.constants import _SESSION_CLARIFICATION_AND_INTENT_TTL_SECONDS
    key = _session_intent_queue_redis_key(session_id)
    await cache_set(key, json.dumps(queue), ttl=_SESSION_CLARIFICATION_AND_INTENT_TTL_SECONDS)
    print(
        "[save_intent_queue]",
        f"session_id={session_id!r}",
        f"entry_count={len(queue)}",
        f"statuses={[e.get('status') for e in queue]}",
    )


async def clear_intent_queue(session_id: str) -> None:
    """Delete the intent queue for a session from Redis."""
    from src.cache import cache_delete
    key = _session_intent_queue_redis_key(session_id)
    await cache_delete(key)
    print(f"[clear_intent_queue] session_id={session_id!r}")


def _sum_line_item_totals(line_items: list[dict]) -> int:
    return sum(int(line_item.get("lineTotal") or 0) for line_item in line_items)


def _hardcoded_sales_tax(subtotal: int) -> int:
    return ((subtotal * _HARDCODED_SALES_TAX_PERCENT) + 50) // 100


def _pricing_breakdown_from_order(order_data: dict) -> dict:
    line_items = [
        _priced_line_item(li) for li in _normalize_order_line_items(order_data)
    ]
    subtotal = order_data.get("subtotal")
    if subtotal is None:
        subtotal = _sum_line_item_totals(line_items)

    tax = _hardcoded_sales_tax(subtotal)
    total = subtotal + tax

    return {
        "lineItems": line_items,
        "subtotal": subtotal,
        "tax": tax,
        "total": total,
        "currency": order_data.get("currency") or "USD",
    }