"""Fixtures for tests that need the local Postgres and Keycloak (`make up`)."""

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import psycopg
import pytest
from halcyon_world import generate_world
from halcyon_world.load import load_world
from httpx import ASGITransport
from query_api.app import create_app
from query_api.config import Settings
from query_api.provision import IdentityUser, load_identity, provision

ROOT = Path(__file__).resolve().parents[4]
IDENTITY_FILE = ROOT / "deploy" / "compose" / "identity" / "users.json"
KEYCLOAK = os.environ.get("KEYCLOAK_URL", "http://localhost:8081")
ISSUER = f"{KEYCLOAK}/realms/halcyon"


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
    """Load the world, then provision roles and row-level security on top of it."""
    load_world(generate_world(seed=7), owner_dsn)
    provision(owner_dsn, identity, login_password=_env("QUERY_DB_PASSWORD"))


@pytest.fixture
def owner(owner_dsn: str) -> Iterator[psycopg.Connection]:
    """A superuser connection: sees every row. Used only to compute expected answers."""
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        yield conn


@pytest.fixture
def settings(query_dsn: str) -> Settings:
    return Settings(
        issuer=ISSUER,
        jwks_url=f"{ISSUER}/protocol/openid-connect/certs",
        dsn=query_dsn,
    )


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://query-api") as http:
            yield http
