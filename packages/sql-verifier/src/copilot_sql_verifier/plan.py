"""Typed QueryPlan and its renderer: code writes the SQL; the model only chooses from the catalogue.

Rendering from a plan removes invented identifiers and ad-hoc joins. Every value becomes a bound
parameter, the mandatory filters are injected, money is split by currency, and plans that would
fan out are refused here, before any SQL exists.
"""

from dataclasses import dataclass, field
from typing import Any, Literal

from copilot_catalogue import Catalogue, Store
from pydantic import BaseModel, ConfigDict, Field

from copilot_sql_verifier.joins import INVERSE, repeats_rows
from copilot_sql_verifier.verifier import VerifyOptions

_IDENT = r"^[a-z_][a-z0-9_]*$"
_AGGREGATES = {"sum": "SUM", "avg": "AVG", "min": "MIN", "max": "MAX", "count": "COUNT"}
_MONEY_AGGS = {"sum", "avg", "min", "max"}


class PlanError(Exception):
    """The plan names something outside the catalogue or would produce a wrong answer."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SelectItem(_Strict):
    column: str  # "alias.column", where the alias is the table name
    agg: Literal["sum", "avg", "min", "max", "count", "count_distinct"] | None = None
    alias: str | None = Field(default=None, pattern=_IDENT)


class PlanFilter(_Strict):
    column: str
    op: Literal["=", "!=", "in", ">=", "<", "is_null", "is_not_null"]
    values: list[str | int | bool] = Field(default_factory=list)


class OrderItem(_Strict):
    column: str  # a select alias or a qualified column
    descending: bool = False


class QueryPlan(_Strict):
    store: str
    joins: list[str] = Field(default_factory=list)  # relationship names from the catalogue
    select: list[SelectItem] = Field(min_length=1)
    filters: list[PlanFilter] = Field(default_factory=list)
    order_by: list[OrderItem] = Field(default_factory=list)
    limit: int = Field(ge=1, le=5000)
    offset: int = Field(default=0, ge=0, le=100_000)  # for follow-ups such as "the next 5"
    include_history: bool = False
    include_cancelled: bool = False
    # Which date the question is about (for example booking_date for period reporting, value_date
    # for cash positioning). Code then checks that the query really filters on it.
    date_field: str | None = Field(default=None, pattern=r"^[a-z_]{1,40}$")


@dataclass(frozen=True)
class RenderedQuery:
    sql: str
    params: list[Any]
    options: VerifyOptions = field(default_factory=VerifyOptions)


class _Render:
    def __init__(self, plan: QueryPlan, catalogue: Catalogue) -> None:
        self.plan = plan
        self.catalogue = catalogue
        self.params: list[Any] = []
        self.scope: dict[str, Store] = {}
        self.joins: list[tuple[str, str, str]] = []  # (alias, alias, cardinality) both directions
        self.join_sql: list[str] = []

    def bind(self, value: Any) -> str:
        self.params.append(value)
        return f"${len(self.params)}"

    # -- scope and columns -----------------------------------------------------------------------

    def build_scope(self) -> None:
        primary = self.catalogue.stores.get(self.plan.store)
        if primary is None:
            raise PlanError("UNKNOWN_STORE", self.plan.store)
        self.scope[primary.table] = primary
        for name in self.plan.joins:
            found = None
            for alias, store in list(self.scope.items()):
                found = next((r for r in store.relationships if r.name == name), None)
                if found:
                    source_alias = alias
                    break
            if found is None:
                raise PlanError("UNKNOWN_JOIN", name)
            target = self.catalogue.store(found.to)
            if target.table in self.scope:
                raise PlanError("UNKNOWN_JOIN", f"{name} joins {target.table} twice")
            self.scope[target.table] = target
            on = " AND ".join(f"{source_alias}.{a} = {target.table}.{b}" for a, b in found.join_on)
            self.join_sql.append(f"JOIN {target.store} AS {target.table} ON {on}")
            self.joins.append((source_alias, target.table, found.cardinality))
            self.joins.append((target.table, source_alias, INVERSE[found.cardinality]))

    def column(self, reference: str) -> tuple[str, str, Store]:
        """Validate `alias.column` and return (alias, column, store)."""
        alias, _, name = reference.partition(".")
        if not name:
            owners = [a for a, s in self.scope.items() if s.column(reference)]
            if len(owners) != 1:
                raise PlanError("UNKNOWN_COLUMN", reference)
            alias, name = owners[0], reference
        store = self.scope.get(alias)
        definition = store.column(name) if store else None
        if store is None or definition is None:
            raise PlanError("UNKNOWN_COLUMN", reference)
        if definition.sensitivity == "restricted":
            raise PlanError("SENSITIVE_COLUMN", reference)
        return alias, name, store

    # -- select --

    def select_sql(self) -> tuple[list[str], list[str]]:
        """Return (select expressions, group-by columns)."""
        expressions: list[str] = []
        plain: list[str] = []
        aggregates = [i for i in self.plan.select if i.agg]
        for item in self.plan.select:
            alias, name, store = self.column(item.column)
            ref = f"{alias}.{name}"
            definition = store.column(name)
            if (
                definition is not None
                and definition.untrusted
                and (
                    (item.agg is None and item.alias)
                    or item.agg not in (None, "count", "count_distinct")
                )
            ):
                # The gateway wraps and taints untrusted text by output column name. A rename or
                # an aggregate (max, min) would carry the text out under another name, unlabelled.
                raise PlanError("UNTRUSTED_COLUMN", f"{ref} must be selected as is")
            if item.agg is None:
                expressions.append(f"{ref} AS {item.alias}" if item.alias else ref)
                plain.append(ref)
                continue
            if item.agg != "count_distinct" and self._fans_out(alias):
                raise PlanError("FAN_OUT", f"{ref} would repeat across a one-to-many join")
            inner = (
                f"COUNT(DISTINCT {ref})"
                if item.agg == "count_distinct"
                else (f"{_AGGREGATES[item.agg]}({ref})")
            )
            expressions.append(f"{inner} AS {item.alias}" if item.alias else inner)
            money = store.money_field(name)
            if money and item.agg in _MONEY_AGGS:
                split = f"{alias}.{money.currency_column}"
                pins = [money.currency_column, *money.pinned_by]
                if (
                    split not in plain
                    and not self._pinned(alias, pins)
                    and not any(f"{alias}.{p}" in plain for p in money.pinned_by)
                ):
                    expressions.insert(0, split)
                    plain.insert(0, split)
        return expressions, (plain if aggregates else [])

    def _fans_out(self, alias: str) -> bool:
        return repeats_rows(alias, self.joins)

    def _pinned(self, alias: str, columns: list[str]) -> bool:
        for f in self.plan.filters:
            a, name, _ = self.column(f.column)
            single = f.op == "=" or (f.op == "in" and len(f.values) == 1)
            if a == alias and name in columns and single:
                return True
        return False

    # -- where --

    def where_sql(self) -> list[str]:
        predicates: list[str] = []
        for f in self.plan.filters:
            alias, name, _ = self.column(f.column)
            ref = f"{alias}.{name}"
            if f.op in ("is_null", "is_not_null"):
                predicates.append(f"{ref} IS {'NOT ' if f.op == 'is_not_null' else ''}NULL")
            elif f.op == "in":
                if not f.values:
                    raise PlanError("BAD_FILTER", f"{f.column} IN needs values")
                predicates.append(f"{ref} IN ({', '.join(self.bind(v) for v in f.values)})")
            else:
                if len(f.values) != 1:
                    raise PlanError("BAD_FILTER", f"{f.column} {f.op} needs exactly one value")
                predicates.append(f"{ref} {f.op.replace('!=', '<>')} {self.bind(f.values[0])}")
        for alias, store in self.scope.items():
            for rule in store.required_filters:
                waived = (rule.waived_by == "history" and self.plan.include_history) or (
                    rule.waived_by == "cancelled" and self.plan.include_cancelled
                )
                if waived:
                    continue
                ref = f"{alias}.{rule.column}"
                predicates.append(ref if rule.value is True else f"{ref} = {self.bind(rule.value)}")
        return predicates

    # -- order --

    def order_sql(self) -> list[str]:
        aliases = {i.alias for i in self.plan.select if i.alias}
        parts = []
        for item in self.plan.order_by:
            ref = (
                item.column
                if item.column in aliases
                else "{}.{}".format(*self.column(item.column)[:2])
            )
            parts.append(f"{ref} DESC" if item.descending else ref)
        return parts


def render(plan: QueryPlan, catalogue: Catalogue) -> RenderedQuery:
    """Render a plan to parameterised SQL, or raise PlanError. Deterministic for a given plan."""
    work = _Render(plan, catalogue)
    work.build_scope()
    expressions, group = work.select_sql()
    predicates = work.where_sql()
    order = work.order_sql()
    primary = catalogue.store(plan.store)
    parts = [f"SELECT {', '.join(expressions)}", f"FROM {primary.store}", *work.join_sql]
    if predicates:
        parts.append("WHERE " + " AND ".join(predicates))
    if group:
        parts.append("GROUP BY " + ", ".join(dict.fromkeys(group)))
    if order:
        parts.append("ORDER BY " + ", ".join(order))
    # One past the cap: the Query API fetches cap + 1 to learn whether the result was cut short.
    parts.append(f"LIMIT {plan.limit + 1}")
    if plan.offset:
        if not order:
            # Without an order, rows can come back differently each time, so pages would overlap.
            raise PlanError("OFFSET_NEEDS_ORDER", "paging needs an order_by")
        parts.append(f"OFFSET {plan.offset}")
    options = VerifyOptions(
        history_requested=plan.include_history,
        include_cancelled=plan.include_cancelled,
        date_field=plan.date_field,
    )
    return RenderedQuery(sql=" ".join(parts), params=work.params, options=options)


__all__ = [
    "OrderItem",
    "PlanError",
    "PlanFilter",
    "QueryPlan",
    "RenderedQuery",
    "SelectItem",
    "render",
]
