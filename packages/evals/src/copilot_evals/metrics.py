"""Rates with denominators and bootstrap confidence intervals (Analytics Copilot.md §11.1)."""

import random
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Rate:
    successes: int
    total: int
    value: float | None
    ci_low: float | None
    ci_high: float | None

    def format(self) -> str:
        if self.value is None or self.ci_low is None or self.ci_high is None:
            return f"{self.successes}/{self.total} (no data)"
        return (
            f"{self.successes}/{self.total} = {self.value:.1%} "
            f"(95% CI {self.ci_low:.1%} to {self.ci_high:.1%})"
        )


def rate(
    outcomes: Sequence[bool], *, seed: int, resamples: int = 2000, alpha: float = 0.05
) -> Rate:
    """Success rate with a percentile bootstrap interval. Deterministic for a given seed."""
    total = len(outcomes)
    if total == 0:
        return Rate(0, 0, None, None, None)
    successes = sum(outcomes)
    rng = random.Random(seed)  # noqa: S311  # resampling, not cryptography
    estimates = sorted(sum(rng.choices(outcomes, k=total)) / total for _ in range(resamples))
    low = estimates[int(resamples * alpha / 2)]
    high = estimates[min(resamples - 1, int(resamples * (1 - alpha / 2)))]
    return Rate(successes, total, successes / total, low, high)
