# Repository Guidelines

## Project Structure & Module Organization
Core application code lives in `src/`. `src/main.py` wires the FastAPI app, lifespan setup, and routers. Feature code is grouped by module, with `src/chatbot/` holding the ordering flow and AI integration, and `src/menu/` handling menu endpoints and loading. Reusable configuration and infrastructure live in files such as `src/config.py`, `src/cache.py`, and `src/firebase.py`. Use `scripts/` for maintenance tasks, `templates/index.html` for the browser UI, `data/` for menu and inventory JSON, `tests/` for API tests, and `alembic/` for migrations.

## Build, Test, and Development Commands
There is no separate build step; use `uv` for environment management and run the app directly.

- `uv sync`: install locked dependencies from `pyproject.toml` and `uv.lock`.
- `uvicorn src.main:app --reload`: start the FastAPI server locally.
- `python scripts/seed_menu.py`: seed Redis with menu and restaurant context.
- `ruff check .`: run linting.
- `ruff check --fix .`: apply safe Ruff fixes.
- `ruff format .`: format Python code.
- `pytest tests/chatbot.py tests/menu.py`: run the current test suite.
- `alembic upgrade head`: apply database migrations.

## Coding Style & Naming Conventions
Target Python 3.13, use 4-space indentation, and keep imports and formatting Ruff-clean. Follow the existing module layout when adding features: `router.py`, `schema.py`, `service.py`, `exceptions.py`, and related helpers under `src/<feature>/`. Use `snake_case` for modules, functions, and variables; `PascalCase` for classes and Pydantic models; uppercase for constants. Keep FastAPI handlers and service entry points `async` to match the current codebase.

## Testing Guidelines
Tests use `pytest` with `fastapi.testclient.TestClient`. Name test functions `test_<behavior>` and place new module-level tests in `tests/<module>.py` to stay consistent with the scaffold script. `pytest` alone currently collects no tests, so use explicit file paths until discovery rules are updated. No coverage threshold is configured; add tests for new routes, conversation-state changes, and menu-loading behavior.

## Commit & Pull Request Guidelines
Recent commits use short, lowercase summaries such as `greeting message` and `pickup time suggestion`. Keep commits focused and use the same concise style. PRs should describe the user-visible change, note any required `.env`, Redis, or seed-data updates, link the relevant issue when available, and include screenshots when changing `templates/index.html` or chat UX behavior.
