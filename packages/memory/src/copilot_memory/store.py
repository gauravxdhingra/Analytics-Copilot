"""The memory port and its in-memory implementation (used by tests and the laptop fallback)."""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

from copilot_memory.models import (
    MAX_PREFERENCES,
    MAX_TURNS_PER_USER,
    TURN_TTL_SECONDS,
    MemoryFailure,
    Owner,
    Preference,
    Turn,
)
from copilot_memory.validation import validate_name, validate_text

Kind = Literal["preference", "saved_definition"]


class MemoryStore(Protocol):
    def save_turn(self, owner: Owner, turn: Turn) -> None: ...
    def recent_turns(self, owner: Owner, conversation_id: str, limit: int) -> list[Turn]: ...
    def history(self, owner: Owner, limit: int) -> list[Turn]: ...
    def clear_history(self, owner: Owner) -> int: ...
    def list_preferences(self, owner: Owner) -> list[Preference]: ...
    def add_preference(
        self, owner: Owner, kind: Kind, name: str | None, text: str
    ) -> Preference: ...
    def delete_preference(self, owner: Owner, pref_id: str) -> bool: ...
    def clear_preferences(self, owner: Owner) -> int: ...


def check_new_preference(kind: Kind, name: str | None, text: str) -> tuple[str | None, str]:
    """Validate what a user wants remembered. Shared by every store, so none can skip it."""
    clean_name = validate_name(name) if kind == "saved_definition" else None
    return clean_name, validate_text(text)


class InMemoryMemoryStore:
    def __init__(self, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._clock = clock
        self._turns: dict[Owner, list[tuple[datetime, Turn]]] = {}
        self._prefs: dict[Owner, list[Preference]] = {}

    def _live(self, owner: Owner) -> list[tuple[datetime, Turn]]:
        cutoff = self._clock() - timedelta(seconds=TURN_TTL_SECONDS)
        kept = [(at, t) for at, t in self._turns.get(owner, []) if at > cutoff]
        self._turns[owner] = kept
        return kept

    def save_turn(self, owner: Owner, turn: Turn) -> None:
        now = self._clock()
        rows = self._live(owner)
        rows.append((now, turn.model_copy(update={"created_at": now})))
        del rows[: max(0, len(rows) - MAX_TURNS_PER_USER)]  # the oldest go first

    def recent_turns(self, owner: Owner, conversation_id: str, limit: int) -> list[Turn]:
        rows = [t for _at, t in self._live(owner) if t.conversation_id == conversation_id]
        return rows[-limit:]

    def history(self, owner: Owner, limit: int) -> list[Turn]:
        return [t for _at, t in reversed(self._live(owner))][:limit]

    def clear_history(self, owner: Owner) -> int:
        return len(self._turns.pop(owner, []))

    def list_preferences(self, owner: Owner) -> list[Preference]:
        return list(self._prefs.get(owner, []))

    def add_preference(self, owner: Owner, kind: Kind, name: str | None, text: str) -> Preference:
        clean_name, clean_text = check_new_preference(kind, name, text)
        existing = self._prefs.setdefault(owner, [])
        if kind == "saved_definition":
            existing[:] = [p for p in existing if not (p.kind == kind and p.name == clean_name)]
        if len(existing) >= MAX_PREFERENCES:
            raise MemoryFailure("LIMIT_REACHED")
        now = self._clock()
        pref = Preference(
            pref_id=str(uuid.uuid4()),
            kind=kind,
            name=clean_name,
            text=clean_text,
            created_at=now,
            updated_at=now,
        )
        existing.append(pref)
        return pref

    def delete_preference(self, owner: Owner, pref_id: str) -> bool:
        existing = self._prefs.get(owner, [])
        kept = [p for p in existing if p.pref_id != pref_id]
        self._prefs[owner] = kept
        return len(kept) != len(existing)

    def clear_preferences(self, owner: Owner) -> int:
        return len(self._prefs.pop(owner, []))
