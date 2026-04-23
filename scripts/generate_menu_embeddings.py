#!/usr/bin/env python3
"""Generate and store menu item embeddings in Firestore.

Usage:
    python scripts/generate_menu_embeddings.py <firebase_merchant_id>

Example:
    python scripts/generate_menu_embeddings.py 2eb00db9-55a3-4ed5-9e0b-105f7496e729

The script:
  1. Reads Clover credentials from Firestore for the given merchant.
  2. Fetches the live menu from Clover.
  3. Embeds every item name via the OpenAI embeddings API.
  4. Writes the result to Firestore at:
       menu_embeddings/{firebase_merchant_id}
     with fields: model, generated_at, menu_hash, embeddings.

Run this once after initial setup, and again whenever the menu changes.
Does NOT require Redis — reads Firestore directly.
"""

import asyncio
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import openai

import src.firebase as firebase_module
from src.chatbot.tools import _clover_integration_doc
from src.chatbot.utils import _normalize_menu
from src.config import settings
from src.firebase import init_firebase
from src.menu.clover_client import ensure_fresh_clover_access_token, fetch_clover_menu

EMBEDDING_MODEL = "text-embedding-3-small"
BATCH_SIZE = 100  # OpenAI supports up to 2048 inputs per request; 100 is safe


async def _get_clover_creds(db, firebase_merchant_id: str) -> dict:
    snapshot = await _clover_integration_doc(db, firebase_merchant_id)
    if not snapshot.exists:
        raise RuntimeError(
            f"No Clover integration found for merchant {firebase_merchant_id!r}"
        )
    creds = snapshot.to_dict() or {}
    base_url = str(creds.get("api_base_url") or settings.CLOVER_API_BASE_URL).rstrip("/")
    token = await ensure_fresh_clover_access_token(
        creds,
        base_url,
        snapshot.reference,
        app_client_id=settings.CLOVER_APP_ID,
    )
    creds["base_url"] = base_url
    creds["token"] = token
    return creds


async def _extract_item_names(raw_menu: dict) -> list[str]:
    """Return item name keys exactly as the main system stores them in by_name."""
    normalized = await _normalize_menu(raw_menu)
    return list(normalized["by_name"].keys())


def _compute_hash(names: list[str]) -> str:
    payload = json.dumps(sorted(names), ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


async def _embed_batch(client: openai.AsyncOpenAI, texts: list[str]) -> list[list[float]]:
    response = await client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
    # Response items are returned in the same order as input
    return [item.embedding for item in sorted(response.data, key=lambda x: x.index)]


async def _embed_all(names: list[str]) -> dict[str, list[float]]:
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set in config / .env")

    client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    embeddings: dict[str, list[float]] = {}

    batches = [names[i : i + BATCH_SIZE] for i in range(0, len(names), BATCH_SIZE)]
    print(f"[embed] {len(names)} items → {len(batches)} batch(es) using {EMBEDDING_MODEL}")

    for idx, batch in enumerate(batches, start=1):
        print(f"[embed] batch {idx}/{len(batches)} ({len(batch)} items)...")
        vectors = await _embed_batch(client, batch)
        for name, vector in zip(batch, vectors):
            embeddings[name] = vector

    return embeddings


async def main(firebase_merchant_id: str) -> None:
    print(f"[generate_menu_embeddings] merchant={firebase_merchant_id!r}")

    await init_firebase()
    db = firebase_module.firebaseDatabase

    print("[generate_menu_embeddings] fetching Clover credentials...")
    creds = await _get_clover_creds(db, firebase_merchant_id)
    clover_merchant_id = creds.get("merchant_id", "")
    print(f"[generate_menu_embeddings] clover_merchant_id={clover_merchant_id!r}")

    print("[generate_menu_embeddings] fetching menu from Clover...")
    raw_menu = await fetch_clover_menu(creds["token"], clover_merchant_id, creds["base_url"])
    item_names = await _extract_item_names(raw_menu)
    print(f"[generate_menu_embeddings] {len(item_names)} menu items found")

    if not item_names:
        print("[generate_menu_embeddings] no items found — aborting")
        return

    menu_hash = _compute_hash(item_names)
    print(f"[generate_menu_embeddings] menu_hash={menu_hash[:12]}...")

    print("[generate_menu_embeddings] generating embeddings...")
    embeddings = await _embed_all(item_names)
    print(f"[generate_menu_embeddings] {len(embeddings)} embeddings generated")

    doc = {
        "model": EMBEDDING_MODEL,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "menu_hash": menu_hash,
        "embeddings": embeddings,
    }

    print("[generate_menu_embeddings] writing to Firestore...")
    await db.collection("menu_embeddings").document(firebase_merchant_id).set(doc)
    print(
        f"[generate_menu_embeddings] done — saved to menu_embeddings/{firebase_merchant_id}"
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/generate_menu_embeddings.py <firebase_merchant_id>")
        sys.exit(1)

    asyncio.run(main(sys.argv[1]))
