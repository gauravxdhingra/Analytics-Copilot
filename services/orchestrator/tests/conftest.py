import os
from collections.abc import Iterator

import pytest
from orchestrator.testing import FakeGateway
from policy_gateway.postgres_ledger import PostgresLedger, ensure_schema


@pytest.fixture
def gateway() -> FakeGateway:
    return FakeGateway()


# --- fixtures for tests that need the local Postgres (`make up`) --------------------------------


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} not set; load deploy/compose/.env")
    return value


@pytest.fixture(scope="session")
def owner_dsn() -> str:
    host = os.environ.get("POSTGRES_HOST", "localhost:5433")
    return f"postgresql://copilot_owner:{_env('POSTGRES_OWNER_PASSWORD')}@{host}/analytics"


@pytest.fixture
def pg_ledger(owner_dsn: str) -> Iterator[PostgresLedger]:
    host = os.environ.get("POSTGRES_HOST", "localhost:5433")
    ensure_schema(owner_dsn, app_password=_env("GATEWAY_DB_PASSWORD"), reset=True)
    yield PostgresLedger(
        f"postgresql://copilot_gateway:{_env('GATEWAY_DB_PASSWORD')}@{host}/analytics"
    )
