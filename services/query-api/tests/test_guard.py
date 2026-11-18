"""Server-side SQL guard. Every rule has a positive and a negative case (sql-and-query-api rule)."""

import pytest
from query_api.guard import QueryRejected, check_sql

ALLOWED = [
    "SELECT 1",
    "SELECT entity_id, sum(amount) FROM treasury.transactions GROUP BY entity_id",
    "SELECT count(*) FROM treasury.transactions WHERE booking_date >= $1 AND booking_date < $2",
    "SELECT t.amount, c.name FROM treasury.transactions t "
    "JOIN treasury.counterparties c USING (counterparty_id)",
    "WITH x AS (SELECT account_id FROM treasury.accounts) SELECT * FROM x",
    "SELECT account_id FROM treasury.accounts UNION ALL SELECT account_id FROM treasury.accounts",
    "SELECT date_trunc('month', booking_date), round(avg(amount), 2) "
    "FROM treasury.transactions GROUP BY 1",
    "SELECT account_id, sum(amount) OVER (PARTITION BY account_id ORDER BY booking_date) "
    "FROM treasury.transactions",
    "SELECT coalesce(max(amount), 0), cast(1 AS numeric) FROM treasury.transactions",
    "SELECT * FROM treasury.accounts WHERE iban LIKE 'DE%'",
    "SELECT * FROM treasury.transactions "
    "WHERE (amount < 0 OR status = 'booked') AND NOT is_current",
    "SELECT * FROM treasury.transactions WHERE amount BETWEEN $1 AND $2 AND status IN ('booked')",
    "SELECT CASE WHEN amount < 0 THEN 'out' ELSE 'in' END, amount * -1, amount + 1 "
    "FROM treasury.transactions WHERE beneficiary_iban IS NOT NULL",
    "SELECT entity_id, sum(amount) FROM treasury.transactions GROUP BY entity_id "
    "HAVING sum(amount) > 0 ORDER BY 2 DESC LIMIT 10 OFFSET 5",
    "SELECT a.account_id FROM treasury.accounts a WHERE EXISTS "
    "(SELECT 1 FROM treasury.transactions t WHERE t.account_id = a.account_id)",
    "SELECT DISTINCT currency FROM treasury.transactions",
    "SELECT t.* FROM treasury.transactions t LEFT JOIN treasury.payments p "
    "ON p.transaction_id = t.transaction_id AND p.transaction_version = t.version",
]


@pytest.mark.parametrize("sql", ALLOWED)
def test_plain_selects_over_treasury_are_allowed(sql: str) -> None:
    check_sql(sql)


def _code(sql: str) -> str:
    with pytest.raises(QueryRejected) as info:
        check_sql(sql)
    return info.value.code


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO treasury.accounts SELECT * FROM treasury.accounts",
        "UPDATE treasury.accounts SET iban = 'x'",
        "DELETE FROM treasury.accounts",
        "DROP TABLE treasury.accounts",
        "CREATE TABLE treasury.x (a int)",
        "ALTER TABLE treasury.accounts ADD COLUMN x int",
        "TRUNCATE treasury.accounts",
        "GRANT ALL ON treasury.accounts TO PUBLIC",
        "COPY treasury.accounts TO PROGRAM 'x'",
        "SET ROLE copilot_owner",
        "RESET ALL",
        "CALL x()",
        "DO $$ BEGIN END $$",
        "EXPLAIN SELECT 1",
        "BEGIN",
        "VACUUM",
    ],
)
def test_anything_but_a_select_is_rejected(sql: str) -> None:
    assert _code(sql) == "NOT_SELECT"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; SELECT 2",
        "SELECT 1; DROP TABLE treasury.accounts",
        "WITH d AS (DELETE FROM treasury.accounts RETURNING *) SELECT * FROM d",
        "WITH i AS (INSERT INTO treasury.accounts SELECT * FROM treasury.accounts RETURNING *) "
        "SELECT * FROM i",
        "SELECT * INTO treasury.copy FROM treasury.accounts",
        "SELECT * FROM treasury.accounts FOR UPDATE",
        "SELECT * FROM treasury.accounts FOR SHARE",
    ],
)
def test_stacked_statements_modifying_ctes_into_and_locks_are_rejected(sql: str) -> None:
    assert _code(sql) == "NOT_SELECT"


@pytest.mark.parametrize("sql", ["", "   ", "-- only a comment", "SELEC 1 FROM", "SELECT ((("])
def test_empty_or_unparseable_input_is_a_syntax_error(sql: str) -> None:
    assert _code(sql) == "SYNTAX"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM accounts",  # unqualified: search_path is not trusted
        "SELECT * FROM public.accounts",
        "SELECT * FROM pg_catalog.pg_roles",
        "SELECT * FROM pg_roles",
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM pg_stats",
        "SELECT * FROM eval.labels",
        "SELECT * FROM docs.documents",
        "SELECT * FROM otherdb.treasury.accounts",
        "SELECT * FROM treasury.accounts WHERE account_id IN (SELECT ref_id FROM eval.labels)",
        "SELECT * FROM (SELECT * FROM pg_catalog.pg_authid) s",
        "SELECT * FROM generate_series(1, 1000000000)",
    ],
)
def test_only_the_treasury_schema_is_readable(sql: str) -> None:
    assert _code(sql) == "PERMISSION"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT set_config('role', 'copilot_owner', true)",
        "SELECT pg_catalog.set_config('app.user', 'x', false)",
        "SELECT pg_sleep(100)",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT lo_import('/etc/passwd')",
        "SELECT dblink('x', 'select 1')",
        "SELECT pg_advisory_lock(1)",
        "SELECT current_setting('app.user')",
        "SELECT pg_terminate_backend(1)",
        "SELECT query_to_xml('select 1', true, true, '')",
        "SELECT version()",
        "SELECT my_custom_function(1)",
    ],
)
def test_functions_outside_the_allowlist_are_rejected(sql: str) -> None:
    assert _code(sql) == "PERMISSION"


def test_cte_names_do_not_open_a_back_door_to_real_tables() -> None:
    assert _code("WITH x AS (SELECT * FROM pg_roles) SELECT * FROM x") == "PERMISSION"
