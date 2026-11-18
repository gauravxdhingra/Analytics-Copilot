"""The model port: what the rest of the system sees of any model provider (ADR 0002)."""

import hashlib
import json
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from copilot_models.errors import ModelError, ModelErrorCode

T = TypeVar("T", bound=BaseModel)


class ModelRole(StrEnum):
    ROUTER = "router"
    PLANNER = "planner"
    NARRATIVE = "narrative"
    EXTRACTOR = "extractor"
    JUDGE = "judge"


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Message(_Strict):
    role: Literal["user", "assistant"]
    # Untrusted content must already be wrapped in <untrusted source="..."> by the caller.
    content: str


class ToolSpec(_Strict):
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema


class ToolCall(_Strict):
    name: str
    arguments: dict[str, Any]


class ModelRequest(_Strict):
    role: ModelRole
    prompt_version: str = Field(min_length=1)  # prompts are versioned (principle 10)
    system: str
    messages: list[Message] = Field(min_length=1)
    output_schema: dict[str, Any] | None = None  # JSON Schema for structured output
    tools: list[ToolSpec] = Field(default_factory=list)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)  # a sampling knob, not money
    max_output_tokens: int = Field(default=1024, ge=1)
    timeout_seconds: float = Field(gt=0)  # every model call has a timeout


class Usage(_Strict):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)  # includes reasoning tokens, which are billed as output
    cost_usd: Decimal | None = None  # None unless a price is configured; never guessed


class ModelResponse(_Strict):
    text: str | None
    tool_calls: list[ToolCall]
    usage: Usage
    model: str
    latency_ms: int = Field(ge=0)
    finish_reason: str

    def parse(self, schema: type[T]) -> T:
        """Validate structured output. The model's text is never echoed in the error."""
        try:
            return schema.model_validate(json.loads(self.text or ""))
        except (ValueError, ValidationError):
            raise ModelError(ModelErrorCode.INVALID_RESPONSE) from None


class ModelClient(Protocol):
    async def generate(self, request: ModelRequest) -> ModelResponse: ...


def request_hash(request: ModelRequest) -> str:
    """Stable identity of a request, ignoring the timeout, which does not change the answer."""
    canonical = json.dumps(
        request.model_dump(mode="json", exclude={"timeout_seconds"}),
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
