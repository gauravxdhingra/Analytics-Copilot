"""Server-side sessions. The browser holds an opaque id; tokens never leave the server."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from bff.pkce import random_token


@dataclass
class Session:
    session_id: str
    subject: str
    username: str
    roles: tuple[str, ...]
    entities: tuple[str, ...]
    tenant_id: str
    csrf_token: str
    access_token: str = field(repr=False)
    refresh_token: str | None = field(repr=False)
    id_token: str | None = field(repr=False)
    access_expires_at: float = 0.0
    acr: str | None = None
    auth_time: int | None = None
    created_at: float = 0.0
    last_seen: float = 0.0


@dataclass
class PendingLogin:
    state: str
    nonce: str
    verifier: str
    return_to: str
    created_at: float
    step_up: bool = False


class SessionStore(Protocol):
    def create(self, session: Session) -> None: ...

    def get(self, session_id: str) -> Session | None: ...

    def delete(self, session_id: str) -> None: ...

    def add_pending(self, pending: PendingLogin) -> None: ...

    def take_pending(self, state: str) -> PendingLogin | None: ...


class InMemorySessionStore:
    """For the laptop profile. A deployed system uses an encrypted shared store (Valkey)."""

    def __init__(
        self,
        *,
        idle_seconds: int,
        absolute_seconds: int,
        login_ttl_seconds: int,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._idle = idle_seconds
        self._absolute = absolute_seconds
        self._login_ttl = login_ttl_seconds
        self._clock = clock
        self._sessions: dict[str, Session] = {}
        self._pending: dict[str, PendingLogin] = {}

    def _now(self) -> float:
        return self._clock().timestamp()

    def create(self, session: Session) -> None:
        session.created_at = session.last_seen = self._now()
        self._sessions[session.session_id] = session

    def get(self, session_id: str) -> Session | None:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        now = self._now()
        if now - session.last_seen > self._idle or now - session.created_at > self._absolute:
            del self._sessions[session_id]
            return None
        session.last_seen = now
        return session

    def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def add_pending(self, pending: PendingLogin) -> None:
        self._pending[pending.state] = pending

    def take_pending(self, state: str) -> PendingLogin | None:
        """Single use: a replayed callback finds nothing."""
        pending = self._pending.pop(state, None)
        if pending is None or self._now() - pending.created_at > self._login_ttl:
            return None
        return pending


def new_session_id() -> str:
    return random_token(32)
