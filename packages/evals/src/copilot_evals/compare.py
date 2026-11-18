"""Programmatic result comparison. Numeric and SQL correctness is never judged by an LLM."""

import re
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

_NUMERIC = re.compile(r"^-?\d+(\.\d+)?$")
_Cell = tuple[str, str]


def _canonical(value: Any) -> _Cell:  # any database or JSON cell
    if value is None:
        return ("null", "")
    if isinstance(value, bool):
        return ("bool", str(value))
    if isinstance(value, float):
        # A float has already lost precision; comparing money through it would hide errors.
        raise TypeError("floats are not valid in results; use Decimal or a numeric string")
    if isinstance(value, int | Decimal):
        return ("num", str(Decimal(value).normalize()))
    if isinstance(value, datetime | date):
        return ("text", value.isoformat())
    text = str(value)
    if _NUMERIC.match(text):
        return ("num", str(Decimal(text).normalize()))
    return ("text", text)


def results_match(
    expected: Sequence[Sequence[Any]], actual: Sequence[Sequence[Any]], *, ordered: bool
) -> bool:
    """True if the two row sets are equal by value. Compares by position, not column name."""
    want = [tuple(_canonical(c) for c in row) for row in expected]
    got = [tuple(_canonical(c) for c in row) for row in actual]
    if ordered:
        return want == got
    return sorted(want) == sorted(got)
