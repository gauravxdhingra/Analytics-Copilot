from decimal import Decimal
from enum import StrEnum
from typing import Any

from copilot_schemas import AgentResult
from pydantic import BaseModel, Field


class AnswerStatus(StrEnum):
    ANSWERED = "answered"
    REFUSED = "refused"
    CLARIFY = "clarify"
    FAILED = "failed"


class AnswerTable(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool = False
    as_of: str | None = None


class Usage(BaseModel):
    model_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: Decimal | None = None


class Answer(BaseModel):
    status: AnswerStatus
    text: str | None = None
    table: AnswerTable | None = None
    sql: str | None = None
    params: list[Any] = Field(default_factory=list)
    refusal_reason: str | None = None  # a mapped code, never raw error text
    clarification: str | None = None
    caveats: list[str] = Field(default_factory=list)
    agent_result: AgentResult | None = None
    usage: Usage = Field(default_factory=Usage)
    steps: list[str] = Field(default_factory=list)
    trace_id: str | None = None  # one trace per run, for the UI's "how I got this"
    # This capability has no write tools, so nothing is ever attempted on untrusted say-so.
    actions: tuple[str, ...] = ()
