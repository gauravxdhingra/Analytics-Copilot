from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class RunCaps:
    """Hard limits on one run, enforced in code. A model can never raise them."""

    max_repair_attempts: int = 3  # MAX_REPAIR_ATTEMPTS
    max_tool_calls: int = 6  # MAX_TOOL_CALLS
    max_cost_usd: Decimal = Decimal("0.50")
    max_wall_clock_s: float = 90.0
    model_timeout_s: float = 30.0

    @property
    def max_plan_attempts(self) -> int:  # MAX_ITERATIONS: the first try plus each repair
        return 1 + self.max_repair_attempts

    def recursion_limit(self) -> int:
        """LangGraph's step limit, derived from the caps. One attempt is plan, render, execute;
        the rest is route, analyse, narrate and finish, plus a little margin."""
        return 3 * self.max_plan_attempts + 4 + 5
