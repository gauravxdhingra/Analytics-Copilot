"""The result cache is per user. One user's rows must never be served to another."""

from datetime import UTC, datetime, timedelta

from copilot_memory import InMemoryResultCache, Owner, cache_key

ANA = Owner(tenant_id="halcyon", user_id="ana")
BEN = Owner(tenant_id="halcyon", user_id="ben")
ANA_ELSEWHERE = Owner(tenant_id="other", user_id="ana")
ROWS = {"columns": ["n"], "rows": [[776]], "as_of": "2026-06-30"}


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 6, 30, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def test_a_hit_returns_what_was_stored() -> None:
    cache = InMemoryResultCache(clock=Clock())
    cache.put(ANA, "k", ROWS)
    assert cache.get(ANA, "k") == ROWS


def test_a_returned_value_cannot_corrupt_the_cached_one() -> None:
    cache = InMemoryResultCache(clock=Clock())
    cache.put(ANA, "k", ROWS)
    got = cache.get(ANA, "k")
    assert got is not None
    got["rows"].append([999])
    assert cache.get(ANA, "k") == ROWS


def test_another_user_or_tenant_never_sees_the_entry() -> None:
    cache = InMemoryResultCache(clock=Clock())
    cache.put(ANA, "k", ROWS)
    assert cache.get(BEN, "k") is None
    assert cache.get(ANA_ELSEWHERE, "k") is None


def test_entries_expire() -> None:
    clock = Clock()
    cache = InMemoryResultCache(clock=clock, ttl_seconds=300)
    cache.put(ANA, "k", ROWS)
    clock.now += timedelta(seconds=299)
    assert cache.get(ANA, "k") == ROWS
    clock.now += timedelta(seconds=2)
    assert cache.get(ANA, "k") is None


def test_one_users_entries_are_bounded_and_the_oldest_go_first() -> None:
    cache = InMemoryResultCache(clock=Clock(), max_entries_per_user=3)
    for i in range(5):
        cache.put(ANA, f"k{i}", {"i": i})
    assert cache.get(ANA, "k0") is None and cache.get(ANA, "k1") is None
    assert cache.get(ANA, "k4") == {"i": 4}


def test_one_user_cannot_push_another_users_entries_out() -> None:
    cache = InMemoryResultCache(clock=Clock(), max_entries_per_user=2)
    cache.put(BEN, "mine", {"ok": True})
    for i in range(50):
        cache.put(ANA, f"k{i}", {"i": i})
    assert cache.get(BEN, "mine") == {"ok": True}


def test_an_oversized_result_is_not_cached() -> None:
    cache = InMemoryResultCache(clock=Clock(), max_value_bytes=100)
    cache.put(ANA, "k", {"blob": "x" * 500})
    assert cache.get(ANA, "k") is None


def test_the_key_depends_on_everything_that_changes_the_answer() -> None:
    base = cache_key("SELECT 1", [1], {"history": False, "cancelled": False})
    assert base == cache_key("SELECT 1", [1], {"history": False, "cancelled": False})
    assert base != cache_key("SELECT 2", [1], {"history": False, "cancelled": False})
    assert base != cache_key("SELECT 1", [2], {"history": False, "cancelled": False})
    assert base != cache_key("SELECT 1", [1], {"history": True, "cancelled": False})
    assert len(base) == 64  # a digest: the SQL itself is never used as a key
