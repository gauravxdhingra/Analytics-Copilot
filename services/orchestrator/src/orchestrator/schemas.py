"""Typed outputs the models must produce. Validated at the boundary (principle 1)."""

from typing import Literal

from copilot_sql_verifier import QueryPlan
from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Route(_Strict):
    intent: Literal["insights", "documents", "unsupported", "ambiguous"]
    clarification: str | None = None
    reason: str = ""


class PlanOut(_Strict):
    plan: QueryPlan | None = None
    clarify: str | None = None
    decline: str | None = None


class NarrativeOut(_Strict):
    text: str = Field(min_length=1, max_length=2000)
