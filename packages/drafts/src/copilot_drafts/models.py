from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DraftStatus(StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"


class EvidenceRef(_Strict):
    source_tool: str = Field(max_length=100)
    source_ref: str = Field(max_length=200)


class DraftPayload(_Strict):
    """What a case-file draft says. Prepared by code from verified results; a human decides."""

    summary: str = Field(min_length=1, max_length=2000)
    recommended_actions: list[str] = Field(default_factory=list, max_length=10)
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=20)


class Draft(_Strict):
    draft_id: str
    kind: str
    entity_id: str
    title: str
    payload: DraftPayload
    status: DraftStatus
    created_by: str
    created_at: datetime
    submitted_by: str | None = None
    submitted_at: datetime | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_comment: str | None = None
    trace_id: str | None = None
