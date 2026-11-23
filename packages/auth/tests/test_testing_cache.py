"""The Keycloak token helper must not hand out a token that is about to expire (long eval runs)."""

from collections.abc import Callable

import pytest
from copilot_auth import testing
from copilot_auth.testing import NOW, keycloak_token_for

pytestmark = pytest.mark.asyncio


class _Source:
    """Stands in for Keycloak: each fetch returns a new token with a chosen lifetime."""

    def __init__(self, make_token: Callable[..., str], lifetime: int) -> None:
        self.fetches = 0
        self._make_token = make_token
        self._lifetime = lifetime

    async def __call__(self, username: str, audience: str) -> str:
        self.fetches += 1
        return self._make_token(
            sub=f"{username}-{self.fetches}", exp=int(NOW.timestamp()) + self._lifetime
        )


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    now = [NOW.timestamp()]
    monkeypatch.setattr(testing, "_now", lambda: now[0])
    monkeypatch.setattr(testing, "_CACHE", {})
    return now


async def test_a_fresh_token_is_reused(
    monkeypatch: pytest.MonkeyPatch, make_token: Callable[..., str], clock: list[float]
) -> None:
    source = _Source(make_token, lifetime=290)
    monkeypatch.setattr(testing, "_fetch", source)
    first = await keycloak_token_for("ana.analyst")
    clock[0] += 100
    assert await keycloak_token_for("ana.analyst") == first
    assert source.fetches == 1


async def test_a_token_near_expiry_is_fetched_again(
    monkeypatch: pytest.MonkeyPatch, make_token: Callable[..., str], clock: list[float]
) -> None:
    source = _Source(make_token, lifetime=290)
    monkeypatch.setattr(testing, "_fetch", source)
    first = await keycloak_token_for("ana.analyst")
    clock[0] += 290 - testing.REFRESH_MARGIN_SECONDS + 1
    second = await keycloak_token_for("ana.analyst")
    assert second != first
    assert source.fetches == 2
