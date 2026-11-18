"""Server-side SQL guard: one plain SELECT over the treasury schema, nothing else.

This is defence in depth. The copilot's own verifier runs first, but this check does not trust
it. Database grants and row-level security are the real boundary; the guard only keeps obviously
dangerous statements from reaching the database at all (ARCHITECTURE.md §8.2, §8.3).
"""

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

ALLOWED_SCHEMA = "treasury"

# Typed function nodes we accept. Anything else (including every unknown function, which sqlglot
# parses as `Anonymous`) is refused, so set_config, pg_sleep, dblink and friends never pass.
_ALLOWED_FUNCTIONS: tuple[type[exp.Expression], ...] = (
    exp.Count,
    exp.Sum,
    exp.Avg,
    exp.Min,
    exp.Max,
    exp.Stddev,
    exp.Coalesce,
    exp.Nullif,
    exp.Greatest,
    exp.Least,
    exp.Abs,
    exp.Round,
    exp.Ceil,
    exp.Floor,
    exp.Lower,
    exp.Upper,
    exp.Length,
    exp.Trim,
    exp.Substring,
    exp.Concat,
    exp.Cast,
    exp.Extract,
    exp.TimestampTrunc,
    exp.DateTrunc,
    exp.CurrentDate,
    exp.CurrentTimestamp,
    exp.RowNumber,
    exp.Rank,
    exp.DenseRank,
    exp.Lag,
    exp.Lead,
    exp.FirstValue,
    exp.LastValue,
    exp.Case,
    exp.If,
    exp.Exists,
    exp.And,
    exp.Or,
    exp.Xor,
)

_FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Command,
    exp.Copy,
    exp.Grant,
    exp.TruncateTable,
    exp.Transaction,
    exp.Set,
    exp.Into,
    exp.Lock,
)

_QUERY_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except)


class QueryRejected(Exception):  # a domain signal, named for what the caller sees
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _identifier_text(node: exp.Expression | None) -> str:
    """Name as Postgres resolves it: unquoted identifiers fold to lower case."""
    if node is None:
        return ""
    name = node.name
    quoted = bool(node.args.get("quoted")) if isinstance(node, exp.Identifier) else False
    return name if quoted else name.lower()


def check_sql(sql: str) -> None:
    """Raise QueryRejected unless `sql` is a single read-only SELECT over allowed objects."""
    try:
        statements = sqlglot.parse(sql, dialect="postgres")
    except SqlglotError as exc:
        raise QueryRejected("SYNTAX") from exc
    parsed = [s for s in statements if s is not None]
    if not parsed:
        raise QueryRejected("SYNTAX")
    if len(parsed) != 1:
        raise QueryRejected("NOT_SELECT")
    root = parsed[0]

    if any(isinstance(node, _FORBIDDEN_NODES) for node in root.walk()):
        raise QueryRejected("NOT_SELECT")
    if not isinstance(root, _QUERY_ROOTS):
        raise QueryRejected("NOT_SELECT")
    for select in root.find_all(exp.Select):
        if select.args.get("into") or select.args.get("locks"):
            raise QueryRejected("NOT_SELECT")

    cte_names = {_identifier_text(cte.args.get("alias")) for cte in root.find_all(exp.CTE)}
    for table in root.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            raise QueryRejected("PERMISSION")  # a table function such as generate_series
        if table.args.get("catalog"):
            raise QueryRejected("PERMISSION")
        schema = _identifier_text(table.args.get("db"))
        if schema:
            if schema != ALLOWED_SCHEMA:
                raise QueryRejected("PERMISSION")
        elif _identifier_text(table.this) not in cte_names:
            raise QueryRejected("PERMISSION")  # unqualified: search_path is not trusted

    for node in root.walk():
        if isinstance(node, exp.Func) and not isinstance(node, _ALLOWED_FUNCTIONS):
            raise QueryRejected("PERMISSION")
