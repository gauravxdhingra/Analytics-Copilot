from datetime import date
from decimal import Decimal

import pytest
from copilot_evals.compare import results_match


def test_numbers_compare_by_value_not_text() -> None:
    assert results_match([["10.50"]], [[Decimal("10.5")]], ordered=True)
    assert results_match([["3"]], [[3]], ordered=True)
    assert not results_match([["10.51"]], [[Decimal("10.5")]], ordered=True)


def test_money_is_never_compared_through_floats() -> None:
    # 0.1 + 0.2 style drift must not make two different exact values equal.
    assert not results_match([["0.30"]], [[Decimal("0.30000000000000004")]], ordered=True)
    with pytest.raises(TypeError):
        results_match([["0.30"]], [[0.3]], ordered=True)


def test_dates_and_text_compare_exactly() -> None:
    assert results_match([["2026-03-01", "C001"]], [[date(2026, 3, 1), "C001"]], ordered=True)
    assert not results_match([["2026-03-01"]], [[date(2026, 3, 2)]], ordered=True)
    assert not results_match([["C001"]], [["c001"]], ordered=True)


def test_ordering_matters_only_when_asked() -> None:
    expected = [["a", "1"], ["b", "2"]]
    shuffled = [["b", 2], ["a", 1]]
    assert results_match(expected, shuffled, ordered=False)
    assert not results_match(expected, shuffled, ordered=True)


def test_duplicates_and_row_counts_matter() -> None:
    assert not results_match([["a"], ["a"]], [["a"]], ordered=False)
    assert not results_match([["a"]], [["a"], ["a"]], ordered=False)
    assert results_match([], [], ordered=False)


def test_null_is_not_the_string_none() -> None:
    assert results_match([[None]], [[None]], ordered=True)
    assert not results_match([["None"]], [[None]], ordered=True)


def test_column_count_must_match() -> None:
    assert not results_match([["a", "b"]], [["a"]], ordered=True)
