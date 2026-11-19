"""Code-side checks and fallbacks for the narrative. The model explains; code verifies."""

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from orchestrator.answer import AnswerTable

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}(?:T[\d:.+-]*)?")
_NUMBER = re.compile(r"(?<![\w.\-])-?\d[\d,]*(?:\.\d+)?")
_CODE = re.compile(r"\b[A-Z]{3}\b")
_COMMON_CURRENCIES = {"EUR", "USD", "GBP", "CHF", "JPY", "CAD", "AUD", "SEK", "NOK", "DKK"}
# What may come before a year for it to be a year: "in June 2026", "Q2 2026", "since 2025".
_YEAR_CONTEXT = re.compile(
    r"(?:\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?|\bq[1-4]|\b(?:in|of|since|during|for|fy))\s*$",
    re.IGNORECASE,
)
_ROW_WORD = re.compile(r"\s*(?:rows?|results?)\b", re.IGNORECASE)
_NEAR_BEFORE = 40  # a currency this far before its number still labels it, if nothing intervenes
_NEAR_AFTER = 2  # "1250000 EUR": the code must sit right after the number


def _normalise(token: str) -> str | None:
    try:
        return str(Decimal(token.replace(",", "")).normalize())
    except InvalidOperation:
        return None


def _mask_dates(text: str) -> str:
    """Blank out ISO dates but keep every position, so number offsets still line up."""
    return _ISO_DATE.sub(lambda m: " " * len(m.group(0)), text)


def _numbers_in(text: str) -> set[str]:
    return {n for t in _NUMBER.findall(_mask_dates(text)) if (n := _normalise(t)) is not None}


def _dates_in(text: str) -> set[str]:
    return {m.group(0)[:10] for m in _ISO_DATE.finditer(text)}


def _currency_column(table: AnswerTable) -> int | None:
    for i, name in enumerate(table.columns):
        values = [row[i] for row in table.rows if row[i] is not None]
        if "currency" in name.lower() or "ccy" in name.lower():
            return i
        if values and all(isinstance(v, str) and re.fullmatch(r"[A-Z]{3}", v) for v in values):
            return i
    return None


def _cell_figures(table: AnswerTable) -> dict[str, set[str | None]]:
    """Every number in the result, with the currency of each row it appears in."""
    currency_at = _currency_column(table)
    figures: dict[str, set[str | None]] = {}
    for row in table.rows:
        code = row[currency_at] if currency_at is not None else None
        for cell in row:
            if isinstance(cell, bool) or cell is None:
                continue
            if isinstance(cell, int | float | Decimal):
                found = {n} if (n := _normalise(str(cell))) is not None else set()
            else:
                found = _numbers_in(str(cell))
            for number in found:
                figures.setdefault(number, set()).add(code if isinstance(code, str) else None)
    return figures


def _label(masked: str, start: int, end: int, known: set[str]) -> str | None:
    """The currency a figure is stated in, by grammar: a code right after it, else the nearest
    code before it with no other figure or code in between. None if the text says nothing."""
    after = (
        _CODE.match(masked, end + 1) if masked[end : end + 1] == " " else _CODE.match(masked, end)
    )
    if after and after.group(0) in known and after.start() - end <= _NEAR_AFTER:
        return after.group(0)
    window_start = max(0, start - _NEAR_BEFORE)
    for m in reversed(list(_CODE.finditer(masked, window_start, start))):
        if m.group(0) not in known:
            continue
        between = masked[m.end() : start]
        if _NUMBER.search(between) or any(c.group(0) in known for c in _CODE.finditer(between)):
            return None
        return m.group(0)
    return None


def numbers_are_grounded(text: str, question: str, table: AnswerTable) -> bool:
    """True if every figure in `text` belongs in the answer. The model never computes, so a number
    it introduces is a hallucination or an unverified calculation. Beyond "is it in the result":

    - a date must be in the result, the question or the snapshot time;
    - the row count only counts when it is said as a count of rows;
    - a number from the question may be echoed, but only as a small count or as a year;
    - a figure stated in a currency must be a figure the result holds in that currency.
    """
    allowed_dates = _dates_in(question) | {
        d for r in table.rows for c in r for d in _dates_in(str(c))
    }
    if table.as_of:
        allowed_dates.add(table.as_of[:10])
    if not _dates_in(text) <= allowed_dates:
        return False

    masked = _mask_dates(text)
    figures = _cell_figures(table)
    known = _COMMON_CURRENCIES | {c for codes in figures.values() for c in codes if c}
    asked = _numbers_in(question)
    for match in _NUMBER.finditer(masked):
        token = match.group(0)
        number = _normalise(token)
        if number is None:
            continue
        if number in figures:
            label = _label(masked, match.start(), match.end(), known)
            if label is not None and label not in figures[number] and figures[number] != {None}:
                return False  # the right number with the wrong currency
            continue
        if number == str(table.row_count) and _ROW_WORD.match(masked, match.end()):
            continue
        if number in asked and _echo_is_fine(token, masked[: match.start()]):
            continue
        return False
    return True


def _echo_is_fine(token: str, before: str) -> bool:
    """A number repeated from the question: a small plain count, or a year stated as a year."""
    if not token.isdigit():
        return False
    value = int(token)
    if len(token) == 4 and 1900 <= value <= 2100:
        return _YEAR_CONTEXT.search(before) is not None
    return value <= 1000


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
