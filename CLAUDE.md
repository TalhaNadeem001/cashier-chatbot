# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the server
uvicorn src.main:app --reload

# Lint and format
ruff check .           # Lint
ruff check --fix .     # Lint and auto-fix
ruff format .          # Format code

# Run tests
pytest

# Database migrations
alembic revision --autogenerate -m "description"  # Generate migration
alembic upgrade head                               # Apply migrations
alembic downgrade -1                               # Roll back one revision
alembic current                                    # Show current revision

# Scaffold a new app module
python scripts/create_app.py <app_name>

# Initialize AI module structure
python scripts/init_ai.py
```

## Architecture

This is a FastAPI backend with async SQLAlchemy, Alembic migrations, Pydantic settings, and uv for package management (Python 3.13).

**App entry point:** `src/main.py` — creates the `FastAPI` app with a lifespan handler that verifies the DB connection on startup and disposes the engine on shutdown. Routers from feature modules are included here.

**Configuration:** `src/config.py` — Pydantic `BaseSettings` (`settings` singleton). Reads `DATABASE_URL` (PostgreSQL asyncpg), `REDIS_URL`, and `ENVIRONMENT` from environment variables or a `.env` file.

**Database:** `src/database.py` — async SQLAlchemy engine (`create_async_engine`), `AsyncSessionLocal` session factory, and `get_db` FastAPI dependency. All models should extend `Base` from this module.

**Module structure:** Each feature lives under `src/<module_name>/` with the files: `router.py`, `schema.py`, `models.py`, `dependencies.py`, `config.py`, `constants.py`, `exceptions.py`, `service.py`, `utils.py`. Use `python scripts/create_app.py <name>` to scaffold this. After scaffolding, register the router in `src/main.py` via `app.include_router(router)`.

**AI module:** `python scripts/init_ai.py` scaffolds `src/ai/` with subdirectories for `clients/`, `prompts/`, `schemas/`, `retrieval/`, `services/`, `tools/local/`, `tools/mcp/`, plus `policies.py`, `config.py`, and `exceptions.py`.

**Alembic:** `alembic/env.py` currently has `target_metadata = None`. Before using autogenerate, wire it to `Base.metadata` from `src.database`.

## Adding Exceptions

1. Define the exception class in the module's `exceptions.py`
2. Add a handler function and register it in the module's `exception_handlers.py`
3. Call `register_exception_handlers(app)` in `src/main.py` (already done for `src/chatbot/`)

`InvalidConversationStateError` subclasses `AIServiceError` — register the subclass handler **before** the base class handler in `register_exception_handlers` so it takes precedence.

## Known basedpyright False Positives

These diagnostics are expected and can be ignored:

- `"request" is not accessed` in FastAPI exception handler functions — `request: Request` is required by the FastAPI exception handler interface even when unused.
- `"app" is not accessed` in the `lifespan` function signature — `app: FastAPI` is required by the FastAPI lifespan interface even when unused.

## Environment Variables

| Variable | Description |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://user:pass@localhost/dbname` |
| `REDIS_URL` | `redis://localhost:6379/0` |
| `ENVIRONMENT` | `development` (default), `staging`, or `production` |

# Fast API Architecure Rule

Make sure every function has async await. Never make a function that is not async await