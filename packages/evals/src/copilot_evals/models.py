from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Behavior(StrEnum):
    ANSWER = "answer"
    REFUSE = "refuse"
    CLARIFY = "clarify"


class Capability(StrEnum):
    INSIGHTS = "insights"
    DOCUMENTS = "documents"


class TrapKind(StrEnum):
    RESTATED_VERSION = "restated_version"
    CANCELLED_AFTER_SNAPSHOT = "cancelled_after_snapshot"
    HALF_OPEN_WINDOW = "half_open_window"
    FAN_OUT = "fan_out"
    CURRENCY_MIX = "currency_mix"
    SCOPE = "scope"
    UNANSWERABLE = "unanswerable"
    AMBIGUOUS = "ambiguous"
    INJECTION = "injection"
    STALE_SNAPSHOT = "stale_snapshot"


class GoldItem(BaseModel):
    """One evaluation case. Expected results are pinned and authored independently of the system."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^GS-\d{3}$")
    capability: Capability
    persona: str  # demo user whose scope the question is asked under
    question: str = Field(min_length=1)
    behavior: Behavior
    trap: TrapKind | None = None
    reference_sql: str | None = None  # hand-written, run only by the oracle, never by the system
    expected_rows: list[list[str | None]] | None = None
    ordered: bool = False
    naive_sql: str | None = None  # the plausible but wrong query a careless agent would write
    must_mention: list[str] = Field(default_factory=list)
    must_not_obey: bool = False  # injection items: no instruction found in data may be followed
    refusal_reason: str | None = None
    notes: str = ""

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.naive_sql is not None and self.trap is None:
            raise ValueError("naive_sql only makes sense for a trap item")
        if self.behavior in (Behavior.REFUSE, Behavior.CLARIFY) and not self.refusal_reason:
            raise ValueError("refuse and clarify items need a refusal_reason")
        needs_pins = self.capability is Capability.INSIGHTS and self.behavior is Behavior.ANSWER
        if needs_pins and (self.reference_sql is None or self.expected_rows is None):
            raise ValueError("an answerable insights item needs reference_sql and expected_rows")
        if self.trap is TrapKind.SCOPE and self.expected_rows != []:
            raise ValueError("a scope item pins an empty result: nothing in scope may be returned")
        return self


class GoldSet(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    world_version: str
    seed: int
    items: list[GoldItem]

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        ids = [item.id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate item ids")
        return self
