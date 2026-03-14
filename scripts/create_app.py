#!/usr/bin/env python3
"""
Create a new FastAPI app module inside src/ and a test file in tests/.

Usage:
    python3 create_app.py app_name
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


def file_content(filename: str, app_name: str, module_name: str) -> str:
    """Return boilerplate content for each file."""
    pascal = to_pascal(module_name)
    templates = {
        "router.py": f'''from fastapi import APIRouter

router = APIRouter(prefix="/{module_name}", tags=["{module_name}"])


@router.get("/")
def list_items():
    """List items."""
    return {{}}
''',
        "schema.py": f'''from pydantic import BaseModel


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
        "models.py": f'''# SQLModel models for {module_name}
# from sqlmodel import SQLModel, Field
#
# class {pascal}(SQLModel, table=True):
#     __tablename__ = "{module_name}"
#     id: int | None = Field(default=None, primary_key=True)
''',
        "dependencies.py": f'''from fastapi import Depends

# Add dependency injection helpers for {module_name}
# def get_service():
#     ...
''',
        "config.py": f'''# App-specific config for {module_name}
# from pydantic_settings import BaseSettings
#
# class {pascal}Settings(BaseSettings):
#     ...
''',
        "constants.py": f'''# Constants for {module_name}
''',
        "exceptions.py": f'''# Custom exceptions for {module_name}
# from src.exceptions import AppException
''',
        "service.py": f'''# Business logic for {module_name}
# class {pascal}Service:
#     ...
''',
        "utils.py": f'''# Helper functions for {module_name}
''',
    }
    return templates.get(filename, "")


def test_file_content(module_name: str) -> str:
    """Return boilerplate for the test file."""
    return f'''import pytest
from fastapi.testclient import TestClient

from src.main import app

client = TestClient(app)


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
    tests_dir = root / "tests"

    files = [
        "router.py",
        "schema.py",
        "models.py",
        "dependencies.py",
        "config.py",
        "constants.py",
        "exceptions.py",
        "service.py",
        "utils.py",
    ]

    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "__init__.py").touch()
    for f in files:
        path = app_dir / f
        if path.exists():
            print(f"  skip (exists): {path.relative_to(root)}")
            continue
        path.write_text(file_content(f, args.app_name, module_name), encoding="utf-8")
        print(f"  created: {path.relative_to(root)}")

    tests_dir.mkdir(parents=True, exist_ok=True)
    test_path = tests_dir / f"{module_name}.py"
    if test_path.exists():
        print(f"  skip (exists): {test_path.relative_to(root)}")
    else:
        test_path.write_text(test_file_content(module_name), encoding="utf-8")
        print(f"  created: {test_path.relative_to(root)}")

    print(f"\nDone. Add the router in src/main.py:")
    print(f'  from src.{module_name}.router import router')
    print(f'  app.include_router(router)')


if __name__ == "__main__":
    main()
