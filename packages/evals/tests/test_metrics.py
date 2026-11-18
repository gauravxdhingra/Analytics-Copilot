import pytest
from copilot_evals.metrics import Rate, rate


def test_rate_reports_numerator_denominator_and_interval() -> None:
    r = rate([True] * 8 + [False] * 2, seed=1)
    assert (r.successes, r.total) == (8, 10)
    assert r.value == pytest.approx(0.8)
    assert r.value is not None and r.ci_low is not None and r.ci_high is not None
    assert 0.0 <= r.ci_low <= r.value <= r.ci_high <= 1.0
    assert r.ci_high - r.ci_low > 0.1  # ten items cannot give a tight interval


def test_interval_is_deterministic_for_a_seed() -> None:
    outcomes = [True, False, True, True, False, True]
    assert rate(outcomes, seed=5) == rate(outcomes, seed=5)


def test_all_pass_and_all_fail_are_handled() -> None:
    assert rate([True] * 5, seed=1).ci_low == 1.0
    assert rate([False] * 5, seed=1).ci_high == 0.0


def test_empty_input_has_no_value() -> None:
    r = rate([], seed=1)
    assert r.total == 0 and r.value is None


def test_more_data_narrows_the_interval() -> None:
    small = rate([True, False] * 5, seed=1)
    large = rate([True, False] * 200, seed=1)
    bounds = [small.ci_low, small.ci_high, large.ci_low, large.ci_high]
    assert all(b is not None for b in bounds)
    assert (large.ci_high or 0) - (large.ci_low or 0) < (small.ci_high or 0) - (small.ci_low or 0)


def test_rate_formats_with_its_denominator() -> None:
    assert "8/10" in Rate(8, 10, 0.8, 0.55, 0.95).format()
