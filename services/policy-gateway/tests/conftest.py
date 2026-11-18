import os
from collections.abc import Callable, Iterator
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from copilot_auth import Principal
from policy_gateway.gateway import Gateway, RunContext
from policy_gateway.ledger import InMemoryLedger
from policy_gateway.policy import Policy
from policy_gateway.postgres_ledger import PostgresLedger, ensure_schema
from policy_gateway.testing import EXAMPLE_POLICY, FakeUpstream, InMemoryTracer


def principal(roles: tuple[str, ...] = ("analyst",)) -> Principal:
    return Principal(
        subject="u-1",
        tenant_id="halcyon",
        entities=("HAL-DE",),
        roles=roles,
        authorized_party="orchestrator",
    )


@pytest.fixture
def policy() -> Policy:
    return Policy.model_validate_policy(EXAMPLE_POLICY)


@pytest.fixture
def upstream() -> FakeUpstream:
    return FakeUpstream()


@pytest.fixture
def ledger() -> InMemoryLedger:
    return InMemoryLedger()


@pytest.fixture
def tracer() -> InMemoryTracer:
    return InMemoryTracer()


@pytest.fixture
def gateway(
    policy: Policy, upstream: FakeUpstream, ledger: InMemoryLedger, tracer: InMemoryTracer
) -> Gateway:
    return Gateway(policy, upstream, ledger, tracer)


@pytest.fixture
def make_ctx() -> Callable[..., RunContext]:
    def build(roles: tuple[str, ...] = ("analyst",), **kwargs: Any) -> RunContext:
        return RunContext(
            run_id=kwargs.pop("run_id", "run-1"),
            trace_id="trace-1",
            principal=principal(roles),
            upstream_tokens={"analytics": "tok-analytics", "drafts": "tok-drafts"},
            **kwargs,
        )

    return build


ZERO = Decimal(0)


# --- fixtures for tests that need the local Postgres (`make up`) ---


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
def gateway_dsn() -> str:
    host = os.environ.get("POSTGRES_HOST", "localhost:5433")
    return f"postgresql://copilot_gateway:{_env('GATEWAY_DB_PASSWORD')}@{host}/analytics"


@pytest.fixture
def pg_ledger(owner_dsn: str, gateway_dsn: str) -> Iterator[PostgresLedger]:
    ensure_schema(owner_dsn, app_password=_env("GATEWAY_DB_PASSWORD"), reset=True)
    yield PostgresLedger(gateway_dsn)


@pytest.fixture
def owner(owner_dsn: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        yield conn
