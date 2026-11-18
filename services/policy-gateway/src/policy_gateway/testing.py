"""Test doubles for the gateway: a scriptable upstream and an in-memory tracer."""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from policy_gateway.gateway import UpstreamError

EXAMPLE_POLICY: dict[str, Any] = {
    "version": 3,
    "agents": {
        "insights.query_planner": {
            "tools": [
                "analytics.describe_store",
                {
                    "analytics.run_validated_select": {
                        "constraints": {"max_rows": 5000, "require_purpose": True},
                        "requires_role": ["analyst", "treasury_manager"],
                    }
                },
            ]
        },
        "recon.remittance_extractor": {"tools": []},
        "recon.adjudicator": {"tools": ["drafts.create_draft"]},
    },
    "defaults": {
        "max_tool_calls_per_run": 4,
        "max_cost_usd_per_run": "2.00",
        "redact": ["iban"],
        "tainted_columns": ["remittance_text"],
    },
}

_SELECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sql": {"type": "string", "minLength": 1},
        "params": {"type": "array"},
        "purpose": {"type": "string", "minLength": 1},
        "expected_grain": {"type": "string"},
        "max_rows": {"type": "integer", "minimum": 1},
    },
    "required": ["sql", "purpose"],
    "additionalProperties": False,
}

SCHEMAS: dict[str, dict[str, Any]] = {
    "analytics.run_validated_select": _SELECT_SCHEMA,
    "analytics.describe_store": {
        "type": "object",
        "properties": {"store_id": {"type": "string"}},
        "required": ["store_id"],
        "additionalProperties": False,
    },
    "drafts.create_draft": {
        "type": "object",
        "properties": {"title": {"type": "string"}, "body": {"type": "string"}},
        "required": ["title", "body"],
        "additionalProperties": False,
    },
}


@dataclass(frozen=True)
class Call:
    tool: str
    args: dict[str, Any]
    token: str


class FakeUpstream:
    def __init__(self) -> None:
        self.calls: list[Call] = []
        self._replies: dict[str, dict[str, Any]] = {}
        self._failures: dict[str, str] = {}

    def reply(self, tool: str, output: dict[str, Any]) -> None:
        self._replies[tool] = output

    def fail(self, tool: str, code: str) -> None:
        self._failures[tool] = code

    @staticmethod
    def rows(row: list[Any], *, untrusted: list[str]) -> dict[str, Any]:
        return {
            "columns": [{"name": "transaction_id"}, {"name": "remittance_text"}],
            "rows": [row],
            "row_count": 1,
            "untrusted_columns": untrusted,
        }

    async def schema(self, tool: str, token: str) -> dict[str, Any]:
        return SCHEMAS.get(tool, {"type": "object"})

    async def call(self, tool: str, args: dict[str, Any], token: str) -> dict[str, Any]:
        self.calls.append(Call(tool, dict(args), token))
        if tool in self._failures:
            raise UpstreamError(self._failures[tool])
        if tool in self._replies:
            return self._replies[tool]
        return {"ok": True}


@dataclass(frozen=True)
class Span:
    name: str
    attributes: Mapping[str, Any]


class _Handle:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    def set(self, values: Mapping[str, Any]) -> None:
        self.values.update(values)


class InMemoryTracer:
    def __init__(self) -> None:
        self.spans: list[Span] = []

    @contextmanager
    def span(self, name: str) -> Iterator[_Handle]:
        handle = _Handle()
        try:
            yield handle
        finally:
            self.spans.append(Span(name, dict(handle.values)))
