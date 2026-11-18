from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from copilot_evals.models import GoldItem


@dataclass(frozen=True)
class AgentAnswer:
    """What an agent produced for one gold item, in a form the scorer can check by code."""

    sql: str | None = None
    rows: list[list[Any]] | None = None  # the result it presented, if any
    text: str | None = None
    refused: bool = False
    clarified: bool = False
    error_code: str | None = None  # a mapped code, never raw error text
    actions: tuple[str, ...] = ()  # side-effecting actions it attempted (always empty for READ)
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: Decimal | None = None


class EvalAgent(Protocol):
    name: str

    async def answer(self, item: GoldItem) -> AgentAnswer: ...
