#!/usr/bin/env python3
"""
Create the AI module structure inside src/ai/.

Usage:
    python3 scripts/init_ai.py
"""
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent.parent
    ai_dir = root / "src" / "ai"

    subdirs = [
        "clients",
        "prompts",
        "schemas",
        "retrieval",
        "services",
        "tools",
        "tools/local",
        "tools/mcp",
    ]

    files = {
        "policies.py": "# AI policies and guardrails\n",
        "config.py": "# AI module config\n",
        "exceptions.py": "# AI-specific exceptions\n",
    }

    ai_dir.mkdir(parents=True, exist_ok=True)
    (ai_dir / "__init__.py").touch()
    print("  created: src/ai/")

    for subdir in subdirs:
        d = ai_dir / subdir
        d.mkdir(parents=True, exist_ok=True)
        (d / "__init__.py").touch()
        print(f"  created: src/ai/{subdir}/")

    for filename, content in files.items():
        path = ai_dir / filename
        if path.exists():
            print(f"  skip (exists): src/ai/{filename}")
            continue
        path.write_text(content, encoding="utf-8")
        print(f"  created: src/ai/{filename}")

    print("\nDone. AI structure at src/ai/")


if __name__ == "__main__":
    main()
