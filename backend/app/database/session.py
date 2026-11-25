"""Async engine helpers. SQLite is the no-Docker local database."""

from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.database.models import Base

_factory: async_sessionmaker[AsyncSession] | None = None


def make_engine(url: str) -> AsyncEngine:
    connect_args: dict = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        if url.startswith("sqlite+aiosqlite:///./"):
            relative = url.removeprefix("sqlite+aiosqlite:///./")
            Path(relative).parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(url, connect_args=connect_args)
    if url.startswith("sqlite"):

        @event.listens_for(engine.sync_engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    return engine


def set_session_factory(factory: async_sessionmaker[AsyncSession]) -> None:
    global _factory
    _factory = factory


def get_factory() -> async_sessionmaker[AsyncSession]:
    if _factory is None:
        raise RuntimeError("Database is not initialized")
    return _factory


async def get_session() -> AsyncIterator[AsyncSession]:
    factory = get_factory()
    async with factory() as session:
        yield session


async def init_database(url: str) -> AsyncEngine:
    engine = make_engine(url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    set_session_factory(async_sessionmaker(engine, expire_on_commit=False))
    return engine
