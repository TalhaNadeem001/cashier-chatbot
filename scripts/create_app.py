#!/usr/bin/env python3
"""
Create a new feature package using the current layered project structure.

Usage:
    python3 scripts/create_app.py app_name
"""
import argparse
import re
from pathlib import Path


def to_snake_case(name: str) -> str:
    """Convert app name to valid Python module name (snake_case)."""
    name = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_")
    return name.lower() or "app"


def to_pascal(name: str) -> str:
    """Convert snake_case to PascalCase for class names."""
    return "".join(word.capitalize() for word in name.split("_"))


def file_content(filename: str, module_name: str) -> str:
    """Return boilerplate content for each file."""
    pascal = to_pascal(module_name)
    templates = {
        "api/router.py": f'''from fastapi import APIRouter

router = APIRouter(prefix="/{module_name}", tags=["{module_name}"])


@router.get("/")
async def list_items():
    """List items."""
    return {{}}
''',
        "api/schema.py": f'''from pydantic import BaseModel


class {pascal}Create(BaseModel):
    """Create schema."""
    pass


class {pascal}Update(BaseModel):
    """Update schema."""
    pass


class {pascal}Response(BaseModel):
    """Response schema."""
    pass
''',
        "application/service.py": f'''class {pascal}Service:
    """Business logic for {module_name}."""

    pass


{module_name}_service = {pascal}Service()
''',
        "domain/types.py": f'''from pydantic import BaseModel


class {pascal}(BaseModel):
    """Domain types for {module_name}."""

    pass
''',
        "infrastructure/repository.py": f'''class {pascal}Repository:
    """Persistence layer for {module_name}."""

    pass


{module_name}_repository = {pascal}Repository()
''',
        "dependencies.py": f'''# Dependency helpers for {module_name}
''',
        "constants.py": f'''# Constants for {module_name}
''',
        "exceptions.py": f'''# Custom exceptions for {module_name}
''',
        "utils.py": f'''# Helper functions for {module_name}
''',
        "__init__.py": "",
        "api/__init__.py": "",
        "application/__init__.py": "",
        "domain/__init__.py": "",
        "infrastructure/__init__.py": "",
    }
    return templates.get(filename, "")


def test_file_content(module_name: str) -> str:
    """Return boilerplate for the test file."""
    return f'''import pytest
from fastapi.testclient import TestClient

from src.app.main import create_app

client = TestClient(create_app(use_lifespan=False))


def test_{module_name}_list():
    """Test {module_name} list endpoint."""
    response = client.get("/{module_name}/")
    assert response.status_code == 200
'''


def main():
    parser = argparse.ArgumentParser(description="Create a new FastAPI app module")
    parser.add_argument("app_name", help="Name of the app (e.g. users, todo_list)")
    args = parser.parse_args()

    module_name = to_snake_case(args.app_name)
    root = Path(__file__).resolve().parent.parent
    app_dir = root / "src" / module_name
    tests_dir = root / "tests" / module_name

    files = [
        "__init__.py",
        "api/__init__.py",
        "api/router.py",
        "api/schema.py",
        "application/__init__.py",
        "application/service.py",
        "domain/__init__.py",
        "domain/types.py",
        "infrastructure/__init__.py",
        "infrastructure/repository.py",
        "dependencies.py",
        "constants.py",
        "exceptions.py",
        "utils.py",
    ]

    for f in files:
        path = app_dir / f
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            print(f"  skip (exists): {path.relative_to(root)}")
            continue
        path.write_text(file_content(f, module_name), encoding="utf-8")
        print(f"  created: {path.relative_to(root)}")

    tests_dir.mkdir(parents=True, exist_ok=True)
    test_path = tests_dir / "test_api.py"
    if test_path.exists():
        print(f"  skip (exists): {test_path.relative_to(root)}")
    else:
        test_path.write_text(test_file_content(module_name), encoding="utf-8")
        print(f"  created: {test_path.relative_to(root)}")

    print("\nDone. Add the router in src/app/api.py:")
    print(f'  from src.{module_name}.api.router import router as {module_name}_router')
    print(f'  app.include_router({module_name}_router)')


if __name__ == "__main__":
    main()
