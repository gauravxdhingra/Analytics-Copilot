import os
from collections.abc import Iterator

import psycopg
import pytest
from copilot_drafts import ensure_schema


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} not set; load deploy/compose/.env")
    return value


def _dsn(user: str, password_env: str) -> str:
    host = os.environ.get("POSTGRES_HOST", "localhost:5433")
    return f"postgresql://{user}:{_env(password_env)}@{host}/analytics"


@pytest.fixture(scope="session")
def owner_dsn() -> str:
    return _dsn("copilot_owner", "POSTGRES_OWNER_PASSWORD")


@pytest.fixture
def drafts_dsn(owner_dsn: str) -> str:
    ensure_schema(
        owner_dsn,
        drafts_password=_env("DRAFTS_DB_PASSWORD"),
        approval_password=_env("APPROVAL_DB_PASSWORD"),
        reset=True,
    )
    return _dsn("copilot_drafts", "DRAFTS_DB_PASSWORD")


@pytest.fixture
def approval_dsn(drafts_dsn: str) -> str:  # depends on drafts_dsn so the schema exists
    return _dsn("copilot_approval", "APPROVAL_DB_PASSWORD")


@pytest.fixture
def owner(owner_dsn: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        yield conn
