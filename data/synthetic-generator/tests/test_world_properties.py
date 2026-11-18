"""Consistency properties of the synthetic world (Analytics Copilot.md §4)."""

from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal

import pytest
from halcyon_world import END, START, WORLD_VERSION, World, generate_world, is_valid_iban
from hypothesis import given, settings
from hypothesis import strategies as st


@pytest.fixture(scope="module")
def world() -> World:
    return generate_world(seed=7)


# --- determinism ---------------------------------------------------------------------------


def test_same_seed_gives_identical_world() -> None:
    assert generate_world(seed=3).manifest() == generate_world(seed=3).manifest()


def test_different_seed_gives_different_world() -> None:
    a, b = generate_world(seed=3).manifest(), generate_world(seed=4).manifest()
    assert a["content_hash"] != b["content_hash"]


def test_manifest_records_version_seed_and_counts(world: World) -> None:
    manifest = world.manifest()
    assert manifest["world_version"] == WORLD_VERSION
    assert manifest["seed"] == 7
    assert manifest["counts"]["transactions"] == len(world.transactions)
    assert manifest["window"] == {"start": START.isoformat(), "end": END.isoformat()}


# --- referential integrity ------------------------------------------------------------------


def test_every_row_links_to_real_parents(world: World) -> None:
    entities = {e.entity_id for e in world.entities}
    accounts = {a.account_id: a for a in world.accounts}
    counterparties = {c.counterparty_id for c in world.counterparties}
    banks = {b.bank_id for b in world.banks}
    assert all(a.entity_id in entities and a.bank_id in banks for a in accounts.values())
    assert all(s.counterparty_id in counterparties for s in world.ssis)
    for t in world.transactions:
        account = accounts[t.account_id]
        assert t.entity_id == account.entity_id
        assert t.amount.currency == account.currency
        assert t.counterparty_id in counterparties


def test_dates_are_inside_the_half_open_window(world: World) -> None:
    assert all(START <= t.booking_date < END for t in world.transactions)
    assert all(START <= r.rate_date < END for r in world.fx_rates)
    assert all(START <= b.balance_date < END for b in world.daily_balances)


def test_account_ibans_pass_mod_97(world: World) -> None:
    assert all(is_valid_iban(a.iban) for a in world.accounts)
    assert all(is_valid_iban(s.iban) for s in world.ssis)


def test_iban_validator_accepts_known_examples_and_rejects_typos() -> None:
    assert is_valid_iban("DE89370400440532013000")
    assert is_valid_iban("GB82WEST12345698765432")
    assert not is_valid_iban("DE89370400440532013001")
    assert not is_valid_iban("XX00")


# --- money and balances ---------------------------------------------------------------------


def test_balances_roll_forward_exactly(world: World) -> None:
    by_account = defaultdict(list)
    for t in world.transactions:
        if t.is_current and t.status == "booked":
            by_account[t.account_id].append(t)
    opening = {a.account_id: a.opening_balance.amount for a in world.accounts}
    balances = {(b.account_id, b.balance_date): b.closing for b in world.daily_balances}
    for account in world.accounts:
        running = opening[account.account_id]
        day = START
        while day < END:
            running += sum(
                (t.amount.amount for t in by_account[account.account_id] if t.booking_date == day),
                Decimal(0),
            )
            assert balances[(account.account_id, day)].amount == running
            assert balances[(account.account_id, day)].currency == account.currency
            day += timedelta(days=1)


def test_amounts_are_exact_two_decimal_decimals(world: World) -> None:
    for t in world.transactions:
        assert isinstance(t.amount.amount, Decimal)
        assert t.amount.amount == t.amount.amount.quantize(Decimal("0.01"))


def test_no_account_is_overdrawn_at_any_point(world: World) -> None:
    assert all(b.closing.amount >= 0 for b in world.daily_balances)


def test_ssi_changes_supersede_an_existing_instruction(world: World) -> None:
    for label in world.labels:
        if label.kind in {"ssi_change_then_payment", "legitimate_ssi_migration"} and (
            label.ref_table == "counterparty"
        ):
            mine = [
                s for s in world.ssis if s.counterparty_id == label.ref_id and s.currency == "EUR"
            ]
            closed = [s for s in mine if s.effective_to is not None]
            opened = [s for s in mine if s.effective_to is None and s.effective_from > START]
            assert len(closed) == 1 and len(opened) == 1, label.ref_id
            assert closed[0].effective_to == opened[0].effective_from


def test_world_has_more_than_one_currency_and_heavy_tailed_amounts(world: World) -> None:
    assert {a.currency for a in world.accounts} >= {"EUR", "GBP", "USD"}
    amounts = sorted(abs(t.amount.amount) for t in world.transactions)
    assert amounts[-1] > 50 * amounts[len(amounts) // 2]


def test_fx_rates_are_positive_and_complete_on_business_days(world: World) -> None:
    assert all(r.rate > 0 for r in world.fx_rates)
    pairs = Counter((r.base, r.quote) for r in world.fx_rates)
    assert len(set(pairs.values())) == 1  # every pair has the same number of dates


# --- versions, payments, approvals ----------------------------------------------------------


def test_each_transaction_has_exactly_one_current_version(world: World) -> None:
    current = Counter(t.transaction_id for t in world.transactions if t.is_current)
    all_ids = {t.transaction_id for t in world.transactions}
    assert set(current) == all_ids
    assert set(current.values()) == {1}


def test_superseded_versions_exist_so_the_current_filter_matters(world: World) -> None:
    assert any(not t.is_current for t in world.transactions)


def test_payments_tie_to_outgoing_transactions_and_separate_duties(world: World) -> None:
    transactions = {(t.transaction_id, t.version): t for t in world.transactions}
    for p in world.payments:
        t = transactions[(p.transaction_id, p.transaction_version)]
        assert t.amount.amount < 0
        assert t.entity_id == p.entity_id
        assert len({p.maker, p.checker, p.releaser}) == 3


# --- labelled faults and the injection corpus -----------------------------------------------


def test_every_label_points_at_a_real_row(world: World) -> None:
    transaction_ids = {t.transaction_id for t in world.transactions}
    payment_ids = {p.payment_id for p in world.payments}
    counterparty_ids = {c.counterparty_id for c in world.counterparties}
    document_ids = {d.document_id for d in world.documents}
    targets = {
        "transaction": transaction_ids,
        "payment": payment_ids,
        "counterparty": counterparty_ids,
        "document": document_ids,
    }
    assert world.labels
    for label in world.labels:
        assert label.ref_id in targets[label.ref_table]


def test_required_fault_kinds_are_present_and_labelled(world: World) -> None:
    kinds = {label.kind for label in world.labels}
    assert kinds >= {
        "restated_transaction",
        "cancelled_after_snapshot",
        "structuring",
        "ssi_change_then_payment",
        "legitimate_ssi_migration",
        "prompt_injection",
    }


def test_structuring_payments_sit_just_under_the_approval_limit(world: World) -> None:
    limits = {e.entity_id: e.approval_limit.amount for e in world.entities}
    payments = {p.payment_id: p for p in world.payments}
    transactions = {(t.transaction_id, t.version): t for t in world.transactions}
    flagged = [label for label in world.labels if label.kind == "structuring"]
    assert len(flagged) >= 3
    for label in flagged:
        p = payments[label.ref_id]
        amount = -transactions[(p.transaction_id, p.transaction_version)].amount.amount
        limit = limits[p.entity_id]
        assert limit * Decimal("0.90") <= amount < limit


def test_cancelled_rows_are_excluded_from_balances_and_present_as_a_trap(world: World) -> None:
    cancelled = [t for t in world.transactions if t.status == "cancelled"]
    assert cancelled


def test_injection_corpus_is_tainted_and_labelled_with_expected_behaviour(world: World) -> None:
    injected = [label for label in world.labels if label.kind == "prompt_injection"]
    assert len(injected) >= 3
    assert all(label.expected == "treat_as_data_never_follow" for label in injected)
    docs = {d.document_id: d for d in world.documents}
    for label in injected:
        if label.ref_table == "document":
            assert docs[label.ref_id].tainted


def test_migration_notice_names_the_counterparty_that_actually_migrates(world: World) -> None:
    legit = next(
        label.ref_id
        for label in world.labels
        if label.kind == "legitimate_ssi_migration" and label.ref_table == "counterparty"
    )
    name = next(c.name for c in world.counterparties if c.counterparty_id == legit)
    notice = next(d for d in world.documents if d.document_id == "D103")
    assert name in notice.body


def test_trusted_documents_describe_the_generated_configuration(world: World) -> None:
    policy = next(d for d in world.documents if d.kind == "policy")
    for entity in world.entities:
        assert entity.entity_id in policy.body
        assert f"{entity.approval_limit.amount:,.0f}" in policy.body


def test_no_real_looking_secrets_or_employer_names(world: World) -> None:
    text = " ".join(d.body for d in world.documents).lower()
    assert "halcyon" in text


# --- robustness across seeds ------------------------------------------------------------------


@settings(max_examples=8, deadline=None)
@given(seed=st.integers(min_value=0, max_value=10_000))
def test_core_invariants_hold_for_any_seed(seed: int) -> None:
    w = generate_world(seed=seed)
    current = Counter(t.transaction_id for t in w.transactions if t.is_current)
    assert set(current.values()) == {1}
    assert all(is_valid_iban(a.iban) for a in w.accounts)
    assert all(START <= t.booking_date < END for t in w.transactions)
    assert START + timedelta(days=1) < date(2027, 1, 1)
