from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

TURN_TTL_SECONDS = 24 * 3600  # short-term memory lives for a day
MAX_TURNS_PER_USER = 200
MAX_PREFERENCES = 20
TEXT_LIMIT = 300


class MemoryFailure(Exception):
    """A mapped failure code. What was rejected is never echoed back."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Owner:
    """Whose memory this is. Both parts come from the validated session, never from a request."""

    tenant_id: str
    user_id: str


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Turn(_Strict):
    """One question and what the code made of it. Never data rows, never model prose."""

    conversation_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")
    question: str = Field(min_length=1, max_length=2000)
    status: str = Field(max_length=20)
    plan: dict[str, Any] | None = None  # the query plan, built and checked by code
    summary: str = Field(max_length=500)  # built by code: status, row count, as-of date
    trace_id: str | None = Field(default=None, max_length=64)
    created_at: datetime | None = None


class Preference(_Strict):
    pref_id: str
    kind: Literal["preference", "saved_definition"]
    name: str | None = None
    text: str
    created_at: datetime
    updated_at: datetime
