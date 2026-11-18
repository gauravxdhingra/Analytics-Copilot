from datetime import UTC, datetime
from decimal import Decimal

import pytest
from copilot_schemas import AgentResult, Claim, CurrencyMismatchError, Evidence, Money, Provenance
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError


def test_money_rejects_float() -> None:
    with pytest.raises(ValidationError):
        Money(amount=1.1, currency="EUR")


def test_money_accepts_decimal_and_numeric_string() -> None:
    assert Money(amount=Decimal("1.10"), currency="EUR").amount == Decimal("1.10")
    assert Money(amount="1.10", currency="EUR").amount == Decimal("1.10")


@pytest.mark.parametrize("bad", ["eur", "EU", "EURO", "E1R", ""])
def test_money_rejects_bad_currency(bad: str) -> None:
    with pytest.raises(ValidationError):
        Money(amount=Decimal(1), currency=bad)


@pytest.mark.parametrize("bad", [Decimal("NaN"), Decimal("Infinity")])
def test_money_rejects_non_finite(bad: Decimal) -> None:
    with pytest.raises(ValidationError):
        Money(amount=bad, currency="EUR")


def test_money_never_sums_across_currencies() -> None:
    with pytest.raises(CurrencyMismatchError):
        _ = Money(amount=Decimal(1), currency="EUR") + Money(amount=Decimal(1), currency="USD")


@given(
    a=st.decimals(min_value=-(10**9), max_value=10**9, places=2),
    b=st.decimals(min_value=-(10**9), max_value=10**9, places=2),
)
def test_money_addition_is_exact_and_commutative(a: Decimal, b: Decimal) -> None:
    x, y = Money(amount=a, currency="EUR"), Money(amount=b, currency="EUR")
    assert (x + y).amount == a + b
    assert x + y == y + x


def test_money_json_round_trip_keeps_precision() -> None:
    m = Money(amount=Decimal("123456789.123456"), currency="JPY")
    assert Money.model_validate_json(m.model_dump_json()) == m


def _evidence(**kw: object) -> Evidence:
    base: dict[str, object] = {
        "evidence_id": "e1",
        "provenance": Provenance.SYSTEM_STATE,
        "source_ref": "q:abc123",
    }
    return Evidence.model_validate(base | kw)


def test_evidence_requires_source_ref() -> None:
    with pytest.raises(ValidationError):
        Evidence.model_validate({"evidence_id": "e1", "provenance": "system_state"})
    with pytest.raises(ValidationError):
        _evidence(source_ref="")


def test_evidence_rejects_unknown_provenance() -> None:
    with pytest.raises(ValidationError):
        _evidence(provenance="made_up")


def test_evidence_snapshot_must_be_timezone_aware() -> None:
    with pytest.raises(ValidationError):
        _evidence(snapshot_at=datetime(2026, 1, 1))
    assert _evidence(snapshot_at=datetime(2026, 1, 1, tzinfo=UTC)).snapshot_at is not None


def test_claim_requires_evidence_and_bounded_confidence() -> None:
    with pytest.raises(ValidationError):
        Claim(text="Cash is 5", evidence=[], confidence=0.9)
    with pytest.raises(ValidationError):
        Claim(text="Cash is 5", evidence=[_evidence()], confidence=1.1)


def test_claim_labelled_unknown_may_have_no_evidence() -> None:
    # "UNKNOWN" claims say the system could not find support, so there is nothing to cite.
    unknown = Evidence(evidence_id="e0", provenance=Provenance.UNKNOWN, source_ref="none")
    assert Claim(text="No data for entity B", evidence=[unknown], confidence=0.0)


def test_agent_result_round_trip_and_extra_forbidden() -> None:
    result = AgentResult(
        run_id="r1",
        capability="insights",
        claims=[Claim(text="Cash is 5", evidence=[_evidence()], confidence=0.8)],
        versions={"prompt": "p1", "model": "m1", "catalogue": "c1", "policy": "y1"},
    )
    assert AgentResult.model_validate_json(result.model_dump_json()) == result
    with pytest.raises(ValidationError):
        AgentResult.model_validate({**result.model_dump(), "surprise": 1})
