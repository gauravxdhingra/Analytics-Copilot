import os
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from halcyon_world import generate_world
from halcyon_world.load import load_world
from query_api.provision import IdentityUser, load_identity, provision

IDENTITY_FILE = (
    Path(__file__).resolve().parents[4] / "deploy" / "compose" / "identity" / "users.json"
)


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} not set; load deploy/compose/.env")
    return value


@pytest.fixture(scope="session")
def owner_dsn() -> str:
    host = os.environ.get("POSTGRES_HOST", "localhost:5433")
    return f"postgresql://copilot_owner:{_env('POSTGRES_OWNER_PASSWORD')}@{host}/analytics"


@pytest.fixture(scope="session")
def query_dsn() -> str:
    host = os.environ.get("POSTGRES_HOST", "localhost:5433")
    return f"postgresql://copilot_query:{_env('QUERY_DB_PASSWORD')}@{host}/analytics"


@pytest.fixture(scope="session")
def identity() -> list[IdentityUser]:
    return load_identity(IDENTITY_FILE)


@pytest.fixture(scope="session", autouse=True)
def database(owner_dsn: str, identity: list[IdentityUser]) -> None:
    load_world(generate_world(seed=7), owner_dsn)
    provision(owner_dsn, identity, login_password=_env("QUERY_DB_PASSWORD"))


@pytest.fixture
def owner(owner_dsn: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        yield conn
