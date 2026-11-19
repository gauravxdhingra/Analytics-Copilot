import os
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from copilot_memory import InMemoryMemoryStore, MemoryStore


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} not set; load deploy/compose/.env")
    return value


def _dsn(user: str, password_env: str) -> str:
    host = os.environ.get("POSTGRES_HOST", "localhost:5433")
    return f"postgresql://{user}:{_env(password_env)}@{host}/analytics"


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 6, 30, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def owner_dsn() -> str:
    return _dsn("copilot_owner", "POSTGRES_OWNER_PASSWORD")


@pytest.fixture
def memory_dsn(owner_dsn: str) -> Iterator[str]:
    from copilot_memory.schema import ensure_schema

    ensure_schema(owner_dsn, memory_password=_env("MEMORY_DB_PASSWORD"))
    with psycopg.connect(owner_dsn) as conn:
        conn.execute("TRUNCATE memory.turns, memory.preferences")
    yield _dsn("copilot_memory", "MEMORY_DB_PASSWORD")


@pytest.fixture(params=["memory", pytest.param("postgres", marks=pytest.mark.integration)])
def backend(request: pytest.FixtureRequest) -> tuple[MemoryStore, Callable[[float], None]]:
    """The same contract runs against both stores. `age(seconds)` moves time forward."""
    if request.param == "memory":
        clock = Clock()

        def age_memory(seconds: float) -> None:
            clock.now += timedelta(seconds=seconds)

        return InMemoryMemoryStore(clock=clock), age_memory

    from copilot_memory.postgres import PostgresMemoryStore

    dsn = request.getfixturevalue("memory_dsn")
    owner = request.getfixturevalue("owner_dsn")

    def age_postgres(seconds: float) -> None:
        with psycopg.connect(owner) as conn:
            conn.execute(
                "UPDATE memory.turns SET expires_at = expires_at - make_interval(secs => %s)",
                (seconds,),
            )

    return PostgresMemoryStore(dsn), age_postgres
