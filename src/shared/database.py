from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from src.shared.config import settings

# ---------- Base ----------
class Base(DeclarativeBase):
    pass

# ---------- Engine ----------
engine = create_async_engine(
    settings.DATABASE_URL or "sqlite+aiosqlite:///:memory:",
    echo=False,             
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
)

# ---------- Session ----------
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# ---------- Dependency ----------
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
