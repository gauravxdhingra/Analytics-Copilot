"""Build and render an eval report. Every rate shows its denominator and a bootstrap interval."""

import json
import math
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from copilot_evals.agents import AgentAnswer
from copilot_evals.metrics import Rate, rate
from copilot_evals.models import Behavior, Capability, GoldSet
from copilot_evals.scoring import ItemResult


def _percentile(values: list[int], fraction: float) -> float | None:
    positive = sorted(v for v in values if v > 0)
    if not positive:
        return None
    return float(positive[max(0, math.ceil(fraction * len(positive)) - 1)])


@dataclass(frozen=True)
class EvalReport:
    agent: str
    gold_name: str
    world_version: str
    seed: int
    metrics: dict[str, Rate]
    by_trap: dict[str, Rate]
    documents: Rate
    scope_leaks: int
    latency_ms_p50: float | None
    latency_ms_p95: float | None
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal | None
    failures: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        def encode(value: Any) -> Any:
            if isinstance(value, Rate):
                return value.__dict__
            if isinstance(value, Decimal):
                return str(value)
            raise TypeError(type(value))

        data = {k: v for k, v in self.__dict__.items()}
        return json.dumps(data, default=encode, indent=2, sort_keys=True)

    def to_markdown(self) -> str:
        lines = [
            f"# Eval report: {self.agent}",
            "",
            f"Gold set `{self.gold_name}`, world {self.world_version}, seed {self.seed}. "
            "Rates show successes/total and a 95% bootstrap interval.",
            "",
            "| Metric | Result |",
            "|---|---|",
        ]
        lines += [f"| {name} | {r.format()} |" for name, r in sorted(self.metrics.items())]
        lines.append(f"| scope_leaks | {self.scope_leaks} |")
        lines.append(f"| documents (reported, not gated) | {self.documents.format()} |")
        if self.latency_ms_p95 is not None:
            lines.append(
                f"| latency p50 / p95 (ms) | {self.latency_ms_p50} / {self.latency_ms_p95} |"
            )
        lines.append(f"| tokens in / out | {self.input_tokens} / {self.output_tokens} |")
        if self.cost_usd is not None:
            lines.append(f"| cost (USD) | {self.cost_usd} |")
        lines += ["", "## Pass rate by trap kind", "", "| Trap | Result |", "|---|---|"]
        lines += [f"| {name} | {r.format()} |" for name, r in sorted(self.by_trap.items())]
        if self.failures:
            lines += ["", "## Failed items", "", ", ".join(self.failures)]
        return "\n".join(lines) + "\n"


def build_report(
    gold: GoldSet,
    results: list[ItemResult],
    answers: list[AgentAnswer],
    *,
    agent: str,
    seed: int,
) -> EvalReport:
    insights = [
        (r, a) for r, a in zip(results, answers, strict=True) if r.capability is Capability.INSIGHTS
    ]
    documents = [r for r in results if r.capability is Capability.DOCUMENTS]
    rs = [r for r, _ in insights]

    answerable = [r for r in rs if r.behavior is Behavior.ANSWER]
    trapped = [r for r in rs if r.trap is not None]
    refusing = [r for r in rs if r.behavior is not Behavior.ANSWER]
    injections = [r for r in rs if r.injection]
    sql_expected = [r for r in answerable if r.valid_sql is not None]

    metrics = {
        "execution_accuracy": rate([r.passed for r in answerable], seed=seed),
        "trap_safety": rate([r.passed for r in trapped], seed=seed),
        "silent_wrong_rate": rate([r.silent_wrong for r in rs], seed=seed),
        "refusal_correctness": rate([r.passed for r in refusing], seed=seed),
        "valid_sql_rate": rate([bool(r.valid_sql) for r in sql_expected], seed=seed),
        "injection_asr": rate([r.obeyed for r in injections], seed=seed),
    }
    kinds = sorted({r.trap.value for r in rs if r.trap})
    by_trap = {
        k: rate([r.passed for r in rs if r.trap and r.trap.value == k], seed=seed) for k in kinds
    }

    costs = [a.cost_usd for _, a in insights if a.cost_usd is not None]
    return EvalReport(
        agent=agent,
        gold_name=gold.name,
        world_version=gold.world_version,
        seed=gold.seed,
        metrics=metrics,
        by_trap=by_trap,
        documents=rate([r.passed for r in documents], seed=seed),
        scope_leaks=sum(r.leaked for r in rs),
        latency_ms_p50=_percentile([a.latency_ms for _, a in insights], 0.5),
        latency_ms_p95=_percentile([a.latency_ms for _, a in insights], 0.95),
        input_tokens=sum(a.input_tokens for _, a in insights),
        output_tokens=sum(a.output_tokens for _, a in insights),
        cost_usd=sum(costs, Decimal(0)) if costs else None,
        failures=[r.item_id for r in results if not r.passed],
    )
