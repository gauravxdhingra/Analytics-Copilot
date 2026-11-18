from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class Provenance(StrEnum):
    SYSTEM_STATE = "system_state"
    DOCUMENT = "document"
    INFERENCE = "inference"
    UNKNOWN = "unknown"


class _Strict(BaseModel):
    # Extra fields are rejected so a model cannot smuggle unvalidated data through a boundary.
    model_config = ConfigDict(extra="forbid")


class Evidence(_Strict):
    evidence_id: str = Field(min_length=1)
    provenance: Provenance
    source_tool: str | None = None  # e.g. "analytics.run_validated_select"
    source_ref: str = Field(min_length=1)  # object id, chunk id or query hash
    snapshot_at: AwareDatetime | None = None
    excerpt: str | None = None  # short and redacted
    trace_span_id: str | None = None


class Claim(_Strict):
    text: str = Field(min_length=1)
    # An unsupported claim is carried by an UNKNOWN-provenance Evidence, never by an empty list.
    evidence: list[Evidence] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class AgentResult(_Strict):
    run_id: str = Field(min_length=1)
    capability: str = Field(min_length=1)
    claims: list[Claim]
    open_questions: list[str] = Field(default_factory=list)
    drafts: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)  # freshness, grain, currency, missing data
    versions: dict[str, str]  # prompt, model, catalogue, policy
