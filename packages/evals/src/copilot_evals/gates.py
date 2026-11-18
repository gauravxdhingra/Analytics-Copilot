"""Release gates (Analytics Copilot.md §11.5). A gate left out of the file is not enforced."""

from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from copilot_evals.report import EvalReport


class Gates(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)  # a misspelled gate is an error

    execution_accuracy_min: float | None = None
    trap_safety_min: float | None = None
    silent_wrong_max: float | None = None
    scope_leaks_max: int | None = None
    injection_asr_max: float | None = None
    latency_p95_ms_max: float | None = None

    @classmethod
    def from_yaml(cls, path: Path) -> "Gates":
        return cls.model_validate(yaml.safe_load(path.read_text()) or {})


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool
    observed: str
    threshold: str
    applicable: bool = True


def _min_rate(name: str, report: EvalReport, key: str, floor: float | None) -> GateResult | None:
    if floor is None:
        return None
    r = report.metrics[key]
    if r.value is None:
        return GateResult(name, True, "no items", f">= {floor:.0%}", applicable=False)
    return GateResult(name, r.value >= floor, r.format(), f">= {floor:.0%}")


def _max_rate(name: str, report: EvalReport, key: str, ceiling: float | None) -> GateResult | None:
    if ceiling is None:
        return None
    r = report.metrics[key]
    if r.value is None:
        return GateResult(name, True, "no items", f"<= {ceiling:.0%}", applicable=False)
    return GateResult(name, r.value <= ceiling, r.format(), f"<= {ceiling:.0%}")


def evaluate_gates(report: EvalReport, gates: Gates) -> list[GateResult]:
    checks = [
        _min_rate("execution_accuracy", report, "execution_accuracy", gates.execution_accuracy_min),
        _min_rate("trap_safety", report, "trap_safety", gates.trap_safety_min),
        _max_rate("silent_wrong_rate", report, "silent_wrong_rate", gates.silent_wrong_max),
        _max_rate("injection_asr", report, "injection_asr", gates.injection_asr_max),
    ]
    results = [c for c in checks if c is not None]
    if gates.scope_leaks_max is not None:
        results.append(
            GateResult(
                "scope_leaks",
                report.scope_leaks <= gates.scope_leaks_max,
                str(report.scope_leaks),
                f"<= {gates.scope_leaks_max}",
            )
        )
    if gates.latency_p95_ms_max is not None:
        p95 = report.latency_ms_p95
        results.append(
            GateResult(
                "latency_p95",
                p95 is None or p95 <= gates.latency_p95_ms_max,
                "no timings" if p95 is None else f"{p95:.0f} ms",
                f"<= {gates.latency_p95_ms_max:.0f} ms",
                applicable=p95 is not None,
            )
        )
    return results
