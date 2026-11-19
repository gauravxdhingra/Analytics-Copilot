from pathlib import Path
from typing import Any

import pytest
from copilot_catalogue import Catalogue, CatalogueError, check_drift

CATALOGUE_DIR = Path(__file__).resolve().parents[3] / "data" / "catalogue"


@pytest.fixture(scope="module")
def catalogue() -> Catalogue:
    return Catalogue.load(CATALOGUE_DIR)


def test_every_store_of_the_synthetic_world_is_documented(catalogue: Catalogue) -> None:
    assert set(catalogue.stores) == {
        "treasury.entities",
        "treasury.banks",
        "treasury.accounts",
        "treasury.counterparties",
        "treasury.ssis",
        "treasury.fx_rates",
        "treasury.transactions",
        "treasury.payments",
        "treasury.daily_balances",
    }


def test_each_store_has_a_definition_grain_and_owner(catalogue: Catalogue) -> None:
    for name, store in catalogue.stores.items():
        assert store.definition, name
        assert store.grain, name
        assert store.owner.steward, name
        columns = {c.name for c in store.columns}
        assert set(store.grain) <= columns, name


def test_transactions_carry_the_mandatory_filters_and_known_issues(catalogue: Catalogue) -> None:
    store = catalogue.store("treasury.transactions")
    assert {(f.column, f.waived_by) for f in store.required_filters} == {
        ("is_current", "history"),
        ("status", "cancelled"),
    }
    assert (
        store.known_issues
    )  # rows cancelled after the snapshot remain, restated rows have two versions


def test_money_columns_name_their_currency_column(catalogue: Catalogue) -> None:
    amount = catalogue.store("treasury.transactions").money_field("amount")
    assert amount is not None and amount.currency_column == "currency"
    assert "account_id" in amount.pinned_by  # one account holds one currency
    assert catalogue.store("treasury.daily_balances").money_field("closing") is not None
    assert catalogue.store("treasury.fx_rates").money_field("rate") is None  # a rate is not money


def test_sensitive_and_untrusted_columns_are_marked(catalogue: Catalogue) -> None:
    iban = catalogue.store("treasury.accounts").column("iban")
    beneficiary = catalogue.store("treasury.transactions").column("beneficiary_iban")
    remittance = catalogue.store("treasury.transactions").column("remittance_text")
    assert iban is not None and iban.sensitivity == "restricted"
    assert beneficiary is not None and beneficiary.sensitivity == "restricted"
    assert remittance is not None and remittance.untrusted
    # Every column filled by an outside party. `name` is matched by column name, so the internal
    # entities.name is wrapped too: over-labelling is the safe direction.
    assert catalogue.untrusted_columns() == {"remittance_text", "reference", "name"}


def test_relationships_are_declared_with_cardinality(catalogue: Catalogue) -> None:
    transactions = catalogue.store("treasury.transactions")
    by_target = {r.to: r for r in transactions.relationships}
    assert by_target["treasury.counterparties"].cardinality == "many_to_one"
    # Both sides repeat: instructions hang off the counterparty, not the transaction.
    assert by_target["treasury.ssis"].cardinality == "many_to_many"  # the fan-out trap
    assert catalogue.relationship("treasury.payments", "treasury.transactions") is not None


def test_every_relationship_points_at_real_columns(catalogue: Catalogue) -> None:
    for store in catalogue.stores.values():
        for rel in store.relationships:
            target = catalogue.store(rel.to)
            for mine, theirs in rel.join_on:
                assert store.column(mine) and target.column(theirs), (store.store, rel.name)


def test_the_glossary_defines_business_terms(catalogue: Catalogue) -> None:
    booked = catalogue.glossary.lookup("Booked")
    assert booked is not None and "booked" in booked.definition.lower()
    assert catalogue.glossary.lookup("outflow") is not None
    assert catalogue.glossary.lookup("no such term") is None


def test_the_version_is_a_stable_content_hash(catalogue: Catalogue) -> None:
    assert catalogue.version == Catalogue.load(CATALOGUE_DIR).version
    assert catalogue.version.startswith("sha256:")


def test_a_relationship_to_an_unknown_store_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_text(
        "store: s.a\ndefinition: d\nowner: {business: b, steward: s}\ngrain: [id]\n"
        "columns: [{name: id, type: text}]\n"
        "relationships: [{name: a.b, to: s.missing, join_on: [[id, id]], cardinality: many_to_one}]\n"
    )
    with pytest.raises(CatalogueError):
        Catalogue.load(tmp_path)


def test_a_grain_column_that_does_not_exist_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_text(
        "store: s.a\ndefinition: d\nowner: {business: b, steward: s}\ngrain: [nope]\n"
        "columns: [{name: id, type: text}]\n"
    )
    with pytest.raises(CatalogueError):
        Catalogue.load(tmp_path)


def test_unknown_keys_are_rejected(tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_text(
        "store: s.a\ndefinition: d\nowner: {business: b, steward: s}\ngrain: [id]\n"
        "columns: [{name: id, type: text}]\nsurprise: 1\n"
    )
    with pytest.raises(CatalogueError):
        Catalogue.load(tmp_path)


# --- schema drift: the catalogue and the warehouse must agree -----------------------------------


def _schema(catalogue: Catalogue) -> dict[str, Any]:
    return {
        "tables": [
            {
                "schema": name.split(".")[0],
                "table": name.split(".")[1],
                "columns": [{"name": c.name, "type": c.type} for c in store.columns],
            }
            for name, store in catalogue.stores.items()
        ]
    }


def test_no_drift_when_they_match(catalogue: Catalogue) -> None:
    assert check_drift(catalogue, _schema(catalogue)) == []


def test_an_undocumented_column_is_reported(catalogue: Catalogue) -> None:
    schema = _schema(catalogue)
    schema["tables"][0]["columns"].append({"name": "surprise", "type": "text"})
    issues = check_drift(catalogue, schema)
    assert any("undocumented" in i and "surprise" in i for i in issues)


def test_a_removed_column_is_reported(catalogue: Catalogue) -> None:
    schema = _schema(catalogue)
    schema["tables"][0]["columns"].pop()
    assert any("removed" in i for i in check_drift(catalogue, schema))


def test_a_changed_type_is_reported(catalogue: Catalogue) -> None:
    schema = _schema(catalogue)
    schema["tables"][0]["columns"][0]["type"] = "bigint"
    assert any("type" in i for i in check_drift(catalogue, schema))


def test_an_undocumented_table_is_reported(catalogue: Catalogue) -> None:
    schema = _schema(catalogue)
    schema["tables"].append({"schema": "treasury", "table": "secret", "columns": []})
    assert any("undocumented table" in i for i in check_drift(catalogue, schema))
