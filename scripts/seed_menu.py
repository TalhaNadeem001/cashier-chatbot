#!/usr/bin/env python3
"""
Seed menu data into Redis for a given user.

Usage:
    python scripts/seed_menu.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import redis.asyncio as aioredis
from src.shared.config import settings
from src.constants import MENU_CONTEXT_STRING, MENU_ITEM_MAP

USER_ID = "1"
MENU_CONTEXT_KEY = f"menu_context:{USER_ID}"
MENU_ITEM_NAMES_KEY = f"menu_item_names:{USER_ID}"
RESTAURANT_NAME_LOCATION_KEY = f"restaurant_name_location:{USER_ID}"
RESTAURANT_NAME_LOCATION_STRING = "The Burger Joint, 123 Main St, Anytown, USA"


async def main():
    client = aioredis.from_url(str(settings.REDIS_URL), decode_responses=True)

    try:
        await client.ping()
        print("Connected to Redis.")

        item_names_string = ", ".join(MENU_ITEM_MAP.keys())

        await client.set(MENU_CONTEXT_KEY, MENU_CONTEXT_STRING)
        print(f"Saved: {MENU_CONTEXT_KEY}")

        await client.set(MENU_ITEM_NAMES_KEY, item_names_string)
        print(f"Saved: {MENU_ITEM_NAMES_KEY}")

        await client.set(RESTAURANT_NAME_LOCATION_KEY, RESTAURANT_NAME_LOCATION_STRING)
        print(f"Saved: {RESTAURANT_NAME_LOCATION_KEY}")

        print(f"\nDone. {len(MENU_ITEM_MAP)} items seeded for user '{USER_ID}'.")
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
