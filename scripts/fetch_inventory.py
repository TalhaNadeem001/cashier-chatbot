import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import src.firebase as firebase_module
from src.firebase import init_firebase

USER_ID = "d60a9acd-53e8-4f6e-87f9-8651e2c29efe"
OUTPUT_PATH = "data/inventory.json"


async def main() -> None:
    await init_firebase()
    db = firebase_module.firebaseDatabase
    col = db.collection("Users").document(USER_ID).collection("Inventory")
    docs = await col.get()
    result = {doc.id: doc.to_dict() for doc in docs}
    with open(OUTPUT_PATH, "w") as f:
        json.dump(result, f, indent=2, default=str)
    print(f"Saved {len(result)} documents to {OUTPUT_PATH}")


asyncio.run(main())
