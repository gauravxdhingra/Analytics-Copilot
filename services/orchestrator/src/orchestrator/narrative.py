"""Code-side checks and fallbacks for the narrative. The model explains; code verifies."""

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from orchestrator.answer import AnswerTable

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}(T[\d:.+-]*)?")
_NUMBER = re.compile(r"(?<![\w.\-])-?\d[\d,]*(?:\.\d+)?")


def _normalise(token: str) -> str | None:
    try:
        return str(Decimal(token.replace(",", "")).normalize())
    except InvalidOperation:
        return None


def _numbers_in(text: str) -> set[str]:
    text = _ISO_DATE.sub(" ", text)
    return {n for t in _NUMBER.findall(text) if (n := _normalise(t)) is not None}


def allowed_numbers(question: str, table: AnswerTable) -> set[str]:
    allowed = _numbers_in(question) | {str(table.row_count)}
    for row in table.rows:
        for cell in row:
            if isinstance(cell, bool) or cell is None:
                continue
            if isinstance(cell, int | float | Decimal):
                if (n := _normalise(str(cell))) is not None:
                    allowed.add(n)
            else:
                allowed |= _numbers_in(str(cell))
    return allowed


def numbers_are_grounded(text: str, question: str, table: AnswerTable) -> bool:
    """True if every number in `text` appears in the question or the result. The model never
    computes, so a number it introduces is a hallucination or an unverified calculation."""
    return _numbers_in(text) <= allowed_numbers(question, table)


def _cell(value: Any) -> str:
    return "none" if value is None else str(value)


def template_text(table: AnswerTable) -> str:
    """A deterministic explanation built only from the result."""
    if table.row_count == 1 and len(table.columns) == 1:
        return f"The result is {_cell(table.rows[0][0])}."
    shown = table.rows[:5]
    lines = [
        "; ".join(f"{c}={_cell(v)}" for c, v in zip(table.columns, row, strict=True))
        for row in shown
    ]
    more = f" (first {len(shown)} shown)" if table.row_count > len(shown) else ""
    return f"The query returned {table.row_count} rows{more}: " + " | ".join(lines) + "."
