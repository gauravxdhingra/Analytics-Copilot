"""A per-user result cache. One user's rows are never served to another.

Behind a port so the cloud profile can use Valkey. This in-process version is for the laptop.
"""

import copy
import hashlib
import json
from collections import OrderedDict
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from copilot_memory.models import Owner


class ResultCache(Protocol):
    def get(self, owner: Owner, key: str) -> dict[str, Any] | None: ...
    def put(self, owner: Owner, key: str, value: dict[str, Any]) -> None: ...


def cache_key(sql: str, params: list[Any], flags: Mapping[str, Any]) -> str:
    """A digest of everything that changes the answer. The SQL is never used as a key itself."""
    body = json.dumps([sql, params, dict(flags)], sort_keys=True, default=str)
    return hashlib.sha256(body.encode()).hexdigest()


class InMemoryResultCache:
    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        ttl_seconds: float = 300,
        max_entries_per_user: int = 64,
        max_users: int = 10_000,
        max_value_bytes: int = 2_000_000,
    ) -> None:
        self._clock = clock
        self._ttl = timedelta(seconds=ttl_seconds)
        self._per_user = max_entries_per_user
        self._max_users = max_users
        self._max_bytes = max_value_bytes
        self._data: OrderedDict[Owner, OrderedDict[str, tuple[datetime, dict[str, Any]]]] = (
            OrderedDict()
        )

    def get(self, owner: Owner, key: str) -> dict[str, Any] | None:
        entries = self._data.get(owner)
        found = entries.get(key) if entries is not None else None
        if entries is None or found is None:
            return None
        expires, value = found
        if self._clock() >= expires:
            del entries[key]
            return None
        return copy.deepcopy(value)  # a caller cannot change what is cached

    def put(self, owner: Owner, key: str, value: dict[str, Any]) -> None:
        if len(json.dumps(value, default=str)) > self._max_bytes:
            return  # too big to hold: just do not cache it
        entries = self._data.setdefault(owner, OrderedDict())
        self._data.move_to_end(owner)
        entries[key] = (self._clock() + self._ttl, copy.deepcopy(value))
        entries.move_to_end(key)
        while len(entries) > self._per_user:
            entries.popitem(last=False)  # this user's oldest, never another user's
        while len(self._data) > self._max_users:
            self._data.popitem(last=False)
