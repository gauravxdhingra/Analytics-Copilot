"""Load a generated world into Postgres. Replaces the treasury, docs and eval schemas."""

import json
from importlib import resources

import psycopg

from halcyon_world.generator import World

_SQL = resources.files("halcyon_world").joinpath("schema.sql")


def _rows(world: World) -> dict[str, tuple[str, list[tuple[object, ...]]]]:
    """Map table name to (column list, rows)."""
    return {
        "treasury.entities": (
            "entity_id, name, country, currency, timezone, approval_limit",
            [
                (e.entity_id, e.name, e.country, e.currency, e.timezone, e.approval_limit.amount)
                for e in world.entities
            ],
        ),
        "treasury.banks": ("bank_id, name, bic", [(b.bank_id, b.name, b.bic) for b in world.banks]),
        "treasury.accounts": (
            "account_id, entity_id, bank_id, iban, currency, opened_on, opening_balance",
            [
                (
                    a.account_id,
                    a.entity_id,
                    a.bank_id,
                    a.iban,
                    a.currency,
                    a.opened_on,
                    a.opening_balance.amount,
                )
                for a in world.accounts
            ],
        ),
        "treasury.counterparties": (
            "counterparty_id, name, country",
            [(c.counterparty_id, c.name, c.country) for c in world.counterparties],
        ),
        "treasury.ssis": (
            "ssi_id, counterparty_id, currency, iban, effective_from, effective_to",
            [
                (s.ssi_id, s.counterparty_id, s.currency, s.iban, s.effective_from, s.effective_to)
                for s in world.ssis
            ],
        ),
        "treasury.fx_rates": (
            "rate_date, base, quote, rate",
            [(r.rate_date, r.base, r.quote, r.rate) for r in world.fx_rates],
        ),
        "treasury.transactions": (
            "transaction_id, version, is_current, entity_id, account_id, counterparty_id, "
            "booking_date, value_date, amount, currency, status, reference, remittance_text, "
            "beneficiary_iban, loaded_at",
            [
                (
                    t.transaction_id,
                    t.version,
                    t.is_current,
                    t.entity_id,
                    t.account_id,
                    t.counterparty_id,
                    t.booking_date,
                    t.value_date,
                    t.amount.amount,
                    t.amount.currency,
                    t.status,
                    t.reference,
                    t.remittance_text,
                    t.beneficiary_iban,
                    t.loaded_at,
                )
                for t in world.transactions
            ],
        ),
        "treasury.payments": (
            "payment_id, transaction_id, transaction_version, entity_id, maker, checker, "
            "releaser, created_at",
            [
                (
                    p.payment_id,
                    p.transaction_id,
                    p.transaction_version,
                    p.entity_id,
                    p.maker,
                    p.checker,
                    p.releaser,
                    p.created_at,
                )
                for p in world.payments
            ],
        ),
        "treasury.daily_balances": (
            "account_id, balance_date, closing, currency",
            [
                (b.account_id, b.balance_date, b.closing.amount, b.closing.currency)
                for b in world.daily_balances
            ],
        ),
        "docs.documents": (
            "document_id, title, kind, body, tainted",
            [(d.document_id, d.title, d.kind, d.body, d.tainted) for d in world.documents],
        ),
        "eval.labels": (
            "kind, ref_table, ref_id, expected",
            [(label.kind, label.ref_table, label.ref_id, label.expected) for label in world.labels],
        ),
    }


def load_world(world: World, dsn: str) -> dict[str, int]:
    """Create the schemas and load every table in one transaction. Returns row counts."""
    counts: dict[str, int] = {}
    with psycopg.connect(dsn) as conn:
        conn.execute(_SQL.read_text())
        for table, (columns, rows) in _rows(world).items():
            with conn.cursor().copy(f"COPY {table} ({columns}) FROM STDIN") as copy:
                for row in rows:
                    copy.write_row(row)
            counts[table] = len(rows)
        manifest = world.manifest()
        conn.execute(
            "INSERT INTO eval.world_manifest VALUES (%s, %s, %s, %s)",
            (
                manifest["world_version"],
                manifest["seed"],
                manifest["content_hash"],
                json.dumps(manifest),
            ),
        )
    return counts
