"""The policy gateway. Final, deterministic authority over every tool call (non-negotiable 2).

Pipeline: identify, authorize, validate, taint check, budget, forward, post-process, ledger and
trace. A model can explain a denial; it can never override one.
"""

import copy
import hashlib
import json
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

from copilot_auth import Principal
from jsonschema import Draft202012Validator

from policy_gateway.content import redact_ibans, strip_hidden, wrap_untrusted
from policy_gateway.ledger import Ledger, LedgerDraft, LedgerError
from policy_gateway.policy import Policy, ToolRule
from policy_gateway.tools import TOOL_TIERS, Tier, server_of
from policy_gateway.tracing import NullTracer, Tracer


class UpstreamError(Exception):
    """A tool server could not be reached or refused. Carries a mapped code, never raw text."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class ToolUpstream(Protocol):
    async def schema(self, tool: str, token: str) -> dict[str, Any]: ...

    async def call(self, tool: str, args: dict[str, Any], token: str) -> dict[str, Any]: ...


@dataclass
class RunState:
    calls: int = 0
    cost: Decimal = Decimal(0)
    plan: set[str] | None = None
    tainted_pending: bool = False
    tainted_values: set[str] = field(default_factory=set)

    def declare_plan(self, tools: set[str]) -> None:
        """A clean re-plan: the orchestrator states which tools it intends to use next."""
        self.plan = set(tools)
        self.tainted_pending = False

    def add_cost(self, amount: Decimal) -> None:
        self.cost += amount


@dataclass
class RunContext:
    run_id: str
    trace_id: str
    principal: Principal
    upstream_tokens: dict[str, str]  # one token per server, each for that server's audience
    state: RunState = field(default_factory=RunState)


@dataclass(frozen=True)
class Allowed:
    output: dict[str, Any]
    tainted: bool = False
    signals: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Denied:
    """PolicyDenied(reason_code). The reason code appears in the ledger and in the trace."""

    reason: str
    detail: str | None = None


ToolOutcome = Allowed | Denied

_MIN_TAINT_MATCH = 8  # shorter untrusted fragments are too common to treat as copied


def _upstream_reason(exc: UpstreamError) -> str:
    return exc.code if exc.code.startswith("UPSTREAM_") else "UPSTREAM_ERROR"


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode()).hexdigest()


def _string_leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _string_leaves(v)]
    if isinstance(value, list | tuple):
        return [s for v in value for s in _string_leaves(v)]
    return []


def _redact_tree(value: Any) -> Any:
    if isinstance(value, str):
        return redact_ibans(value)
    if isinstance(value, dict):
        return {k: _redact_tree(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_tree(v) for v in value]
    return value


class Gateway:
    def __init__(
        self,
        policy: Policy,
        upstream: ToolUpstream,
        ledger: Ledger,
        tracer: Tracer | None = None,
    ) -> None:
        self._policy = policy
        self._upstream = upstream
        self._ledger = ledger
        self._tracer = tracer or NullTracer()

    async def call(
        self, ctx: RunContext, agent_id: str, tool: str, args: dict[str, Any]
    ) -> ToolOutcome:
        with self._tracer.span("tool.call") as span:
            started = time.monotonic()
            outcome, result_hash, rows = await self._decide(ctx, agent_id, tool, args)
            if isinstance(outcome, Allowed) or outcome.reason != "LEDGER_FAILURE":
                try:
                    self._ledger.append(
                        self._draft(ctx, agent_id, tool, args, outcome, result_hash)
                    )
                except LedgerError:
                    outcome = Denied("LEDGER_FAILURE")  # no record, no result
            span.set(self._span_attributes(ctx, agent_id, tool, args, outcome, rows, started))
        return outcome

    # -- the pipeline ------------------------------------------------------------------------

    async def _decide(
        self, ctx: RunContext, agent: str, tool: str, args: dict[str, Any]
    ) -> tuple[ToolOutcome, str | None, int | None]:
        denied = await self._checks(ctx, agent, tool, args)
        if isinstance(denied, Denied):
            return denied, None, None
        _rule, safe_args = denied
        ctx.state.calls += 1
        server = server_of(tool)
        token = ctx.upstream_tokens.get(server)
        if token is None:
            return Denied("UPSTREAM_AUTH"), None, None
        try:
            raw = await self._upstream.call(tool, safe_args, token)
        except UpstreamError as exc:
            return Denied(_upstream_reason(exc), exc.detail), None, None
        allowed = self._post_process(ctx, tool, raw)
        rows = allowed.output.get("row_count")
        return allowed, _digest(allowed.output), rows if isinstance(rows, int) else None

    async def _checks(
        self, ctx: RunContext, agent: str, tool: str, args: dict[str, Any]
    ) -> tuple[ToolRule, dict[str, Any]] | Denied:
        if tool not in TOOL_TIERS:
            return Denied("TOOL_UNKNOWN")
        rule = self._policy.rule(agent, tool)
        if rule is None:
            return Denied("TOOL_NOT_ALLOWED")
        if rule.requires_role and not set(rule.requires_role) & set(ctx.principal.roles):
            return Denied("ROLE_REQUIRED")
        defaults = self._policy.defaults
        if ctx.state.calls >= defaults.max_tool_calls_per_run:
            return Denied("BUDGET_CALLS")
        if ctx.state.cost > defaults.max_cost_usd_per_run:
            return Denied("BUDGET_COST")

        token = ctx.upstream_tokens.get(server_of(tool))
        if token is None:
            return Denied("UPSTREAM_AUTH")
        try:
            schema = await self._upstream.schema(tool, token)
        except UpstreamError as exc:
            return Denied(_upstream_reason(exc), exc.detail)
        # No tool takes arguments it did not declare: nothing is smuggled through the gateway.
        if "properties" in schema:
            schema = {**schema, "additionalProperties": schema.get("additionalProperties", False)}
        if next(Draft202012Validator(schema).iter_errors(args), None) is not None:
            return Denied("INVALID_ARGS")
        safe_args = dict(args)
        limits = rule.constraints
        if limits.require_purpose and not str(args.get("purpose", "")).strip():
            return Denied("CONSTRAINT_PURPOSE")
        if limits.max_rows is not None:
            if int(args.get("max_rows", limits.max_rows)) > limits.max_rows:
                return Denied("CONSTRAINT_MAX_ROWS")
            safe_args.setdefault("max_rows", limits.max_rows)

        state = ctx.state
        if state.tainted_pending and (state.plan is None or tool not in state.plan):
            return Denied("TAINT_REPLAN")
        if TOOL_TIERS[tool] is Tier.DRAFT and self._copies_untrusted(state, args):
            return Denied("TAINT_ARGS")
        return rule, safe_args

    @staticmethod
    def _copies_untrusted(state: RunState, args: dict[str, Any]) -> bool:
        for leaf in _string_leaves(args):
            for value in state.tainted_values:
                if len(value) >= _MIN_TAINT_MATCH and (value in leaf or leaf == value):
                    return True
        return False

    def _post_process(self, ctx: RunContext, tool: str, raw: dict[str, Any]) -> Allowed:
        output = copy.deepcopy(raw)
        signals: list[str] = []
        tainted = False
        columns = [c["name"] for c in output.get("columns", [])]
        untrusted = set(output.get("untrusted_columns", [])) | (
            set(self._policy.defaults.tainted_columns) & set(columns)
        )
        for row in output.get("rows", []):
            for index, name in enumerate(columns):
                value = row[index]
                if name not in untrusted or not isinstance(value, str):
                    continue
                cleaned, removed = strip_hidden(value)
                if removed and "hidden_content_stripped" not in signals:
                    signals.append("hidden_content_stripped")
                ctx.state.tainted_values.add(cleaned)
                row[index] = wrap_untrusted(redact_ibans(cleaned), f"{tool}:{name}")
                tainted = True
        output = _redact_tree(output)
        if tainted:
            ctx.state.tainted_pending = True
        return Allowed(output=output, tainted=tainted, signals=signals)

    # -- ledger and trace --------------------------------------------------------------------

    def _draft(
        self,
        ctx: RunContext,
        agent: str,
        tool: str,
        args: dict[str, Any],
        outcome: ToolOutcome,
        result_hash: str | None,
    ) -> LedgerDraft:
        denied = isinstance(outcome, Denied)
        return LedgerDraft(
            run_id=ctx.run_id,
            trace_id=ctx.trace_id,
            tenant_id=ctx.principal.tenant_id,
            agent_id=agent,
            user_id=ctx.principal.subject,
            tool=tool,
            args_hash=_digest(args),
            args_redacted=_redact_tree(args),
            decision="DENY" if denied else "ALLOW",
            reason_code=outcome.reason if isinstance(outcome, Denied) else None,
            policy_version=self._policy.version,
            result_hash=result_hash,
        )

    def _span_attributes(
        self,
        ctx: RunContext,
        agent: str,
        tool: str,
        args: dict[str, Any],
        outcome: ToolOutcome,
        rows: int | None,
        started: float,
    ) -> dict[str, Any]:
        # Hashes and codes only: no SQL text, argument values or restricted data in a span.
        return {
            "tool": tool,
            "server": server_of(tool),
            "agent": agent,
            "run_id": ctx.run_id,
            "decision": "DENY" if isinstance(outcome, Denied) else "ALLOW",
            "reason": outcome.reason if isinstance(outcome, Denied) else None,
            "policy_version": self._policy.version,
            "args_hash": _digest(args),
            "rows": rows,
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
