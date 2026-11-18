"""Deterministic SQL verification against the semantic catalogue (Analytics Copilot.md §6.5).

Pure code, no model. Every rule here is a gate a model can explain but never override. The
rules are the finance traps made executable: current versions, cancelled rows, fan-out through
one-to-many joins, money added across currencies, and closed date windows.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import sqlglot
from copilot_catalogue import Catalogue, Store
from pydantic import BaseModel
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.scope import Scope, traverse_scope

_AGGREGATES = (exp.Sum, exp.Avg, exp.Min, exp.Max)
_FORBIDDEN = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Command,
    exp.Copy,
    exp.Set,
    exp.Into,
    exp.Lock,
)
_MAX_STRUCTURAL_INT = 100
_NOT_CHECKED = (
    "not checked here: the semantic (model) review, the cost guard and the empirical fan-out "
    "count; the Query API still enforces read-only, timeouts, row caps and row-level security"
)


class Violation(BaseModel):
    code: str
    message: str


class VerificationFailed(Exception):  # a domain signal
    """The query broke one or more rules. All violations are returned together."""

    code = "VERIFIER_REJECTED"

    def __init__(self, violations: list[Violation]) -> None:
        super().__init__(self.code)
        self.violations = violations


@dataclass(frozen=True)
class VerifyOptions:
    history_requested: bool = False  # waives the current-version filter
    include_cancelled: bool = False  # waives the booked-only filter
    allow_restricted: bool = False  # purpose and scope allow restricted columns
    date_field: str | None = None  # the date semantics the question asked for


@dataclass
class _Ref:
    alias: str
    store: Store
    column: str


def _flatten_and(node: exp.Expr | None) -> list[exp.Expr]:
    if node is None:
        return []
    if isinstance(node, exp.Paren):
        return _flatten_and(node.this)
    if isinstance(node, exp.And):
        return _flatten_and(node.left) + _flatten_and(node.right)
    return [node]


class Verifier:
    def __init__(self, catalogue: Catalogue) -> None:
        self._catalogue = catalogue

    def verify(
        self, sql: str, params: Sequence[Any] = (), options: VerifyOptions | None = None
    ) -> list[str]:
        """Return notes on what was not checked, or raise VerificationFailed."""
        run = _Run(self._catalogue, list(params), options or VerifyOptions())
        run.execute(sql)
        if run.violations:
            raise VerificationFailed(run.violations)
        return [_NOT_CHECKED]


class _Run:
    def __init__(self, catalogue: Catalogue, params: list[Any], options: VerifyOptions) -> None:
        self.catalogue = catalogue
        self.params = params
        self.options = options
        self.violations: list[Violation] = []
        self._seen: set[tuple[str, str]] = set()

    def fail(self, code: str, message: str) -> None:
        if (code, message) not in self._seen:
            self._seen.add((code, message))
            self.violations.append(Violation(code=code, message=message))

    # -- entry ---------------------------------------------------------------------------------

    def execute(self, sql: str) -> None:
        try:
            statements = [s for s in sqlglot.parse(sql, dialect="postgres") if s is not None]
        except SqlglotError:
            self.fail("SYNTAX", "The SQL could not be parsed.")
            return
        if not statements:
            self.fail("SYNTAX", "The SQL is empty.")
            return
        root = statements[0]
        if len(statements) > 1 or not isinstance(
            root, exp.Select | exp.Union | exp.Intersect | exp.Except
        ):
            self.fail("NOT_SELECT", "Exactly one SELECT statement is allowed.")
            return
        if any(isinstance(n, _FORBIDDEN) for n in root.walk()):
            self.fail("NOT_SELECT", "The statement modifies data or session state.")
            return
        self._check_parameters(root)
        self._check_literals(root)
        for scope in traverse_scope(root):
            if isinstance(scope.expression, exp.Select):
                _ScopeCheck(self, scope).execute()

    # -- parameters and literals ---------------------------------------------------------------

    def _check_parameters(self, root: exp.Expr) -> None:
        for parameter in root.find_all(exp.Parameter):
            index = int(parameter.this.name)
            if not 1 <= index <= len(self.params):
                self.fail("PARAMS", f"Placeholder ${index} has no bound value.")

    def _check_literals(self, root: exp.Expr) -> None:
        for literal in root.find_all(exp.Literal):
            parent = literal.parent
            if isinstance(parent, exp.Parameter | exp.Limit | exp.Offset | exp.Fetch):
                continue
            if not literal.is_string:
                try:
                    number = float(literal.name)
                except ValueError:
                    number = float("inf")
                if number.is_integer() and abs(number) <= _MAX_STRUCTURAL_INT:
                    continue  # a structural constant such as 0, 1 or the 2 in round(x, 2)
            self.fail(
                "LITERAL",
                f"The literal {literal.sql(dialect='postgres')} must be passed as a parameter.",
            )

    def value_of(self, node: exp.Expr) -> tuple[bool, Any]:
        """(known, value) for a constant: a literal, boolean or bound parameter."""
        if isinstance(node, exp.Paren):
            return self.value_of(node.this)
        if isinstance(node, exp.Boolean):
            return True, node.this
        if isinstance(node, exp.Literal):
            return True, node.name
        if isinstance(node, exp.Neg):
            return self.value_of(node.this)
        if isinstance(node, exp.Parameter):
            index = int(node.this.name)
            if 1 <= index <= len(self.params):
                return True, self.params[index - 1]
        return False, None


class _ScopeCheck:
    """Rules that apply within one SELECT."""

    def __init__(self, run: _Run, scope: Scope) -> None:
        self.run = run
        self.scope = scope
        self.select: exp.Select = scope.expression  # type: ignore[assignment]
        self.stores: dict[str, Store] = {}
        self.derived: set[str] = set()
        self.atoms: list[exp.Expr] = []
        self.joined: list[tuple[str, str, str]] = []  # (alias_a, alias_b, cardinality a->b)

    # -- setup ---------------------------------------------------------------------------------

    def execute(self) -> None:
        self._load_sources()
        self._collect_atoms()
        self._check_columns_and_sensitivity()
        self._check_joins()
        self._check_required_filters()
        self._check_aggregates()
        self._check_dates()

    def _load_sources(self) -> None:
        for alias, source in self.scope.sources.items():
            if isinstance(source, exp.Table):
                key = f"{source.db}.{source.name}".lower() if source.db else source.name.lower()
                store = self.run.catalogue.stores.get(key)
                if store is None:
                    self.run.fail("UNKNOWN_STORE", f"{key} is not in the catalogue.")
                else:
                    self.stores[alias] = store
            else:
                self.derived.add(alias)

    def _collect_atoms(self) -> None:
        where = self.select.args.get("where")
        self.atoms = _flatten_and(where.this if where else None)
        for join in self.select.args.get("joins") or []:
            self.atoms += _flatten_and(join.args.get("on"))

    # -- columns ---------------------------------------------------------------------------------

    def resolve(self, column: exp.Column) -> _Ref | None:
        name = column.name
        if column.table:
            store = self.stores.get(column.table)
            return _Ref(column.table, store, name) if store else None
        owners = [a for a, s in self.stores.items() if s.column(name)]
        if len(owners) >= 1:
            return _Ref(owners[0], self.stores[owners[0]], name)
        return None

    def _select_aliases(self) -> set[str]:
        return {s.alias for s in self.select.selects if s.alias}

    def _check_columns_and_sensitivity(self) -> None:
        restricted_ok = self.run.options.allow_restricted
        for column in self.scope.columns:
            ref = self.resolve(column)
            if ref is None:
                self._unresolved(column)
                continue
            definition = ref.store.column(ref.column)
            where = f"{ref.store.store}.{ref.column}"
            if definition is None:
                self.run.fail("UNKNOWN_COLUMN", f"{where} is not in the catalogue.")
            elif definition.sensitivity == "restricted" and not restricted_ok:
                self.run.fail("SENSITIVE_COLUMN", f"{where} is restricted.")
        for star in self.select.find_all(exp.Star):
            if star.find_ancestor(exp.Select) is not self.select or isinstance(
                star.parent, exp.Count
            ):
                continue
            qualifier = star.parent.table if isinstance(star.parent, exp.Column) else None
            targets = list(self.stores.values())
            if qualifier in self.stores:
                targets = [self.stores[qualifier]]
            for store in targets:
                exposes = any(c.sensitivity == "restricted" for c in store.columns)
                if exposes and not restricted_ok:
                    self.run.fail(
                        "SENSITIVE_COLUMN",
                        f"SELECT * on {store.store} would expose restricted columns.",
                    )

    def _unresolved(self, column: exp.Column) -> None:
        if column.table and column.table in self.stores:
            self.run.fail(
                "UNKNOWN_COLUMN", f"{column.table}.{column.name} is not in the catalogue."
            )
            return
        is_alias = column.name in self._select_aliases()
        if not column.table and not is_alias and self.stores and not self.derived:
            self.run.fail("UNKNOWN_COLUMN", f"{column.name} is not in the catalogue.")

    # -- joins -----------------------------------------------------------------------------------

    def _equalities(self, node: exp.Expr | None) -> list[tuple[_Ref, _Ref]]:
        pairs = []
        for atom in _flatten_and(node):
            if (
                isinstance(atom, exp.EQ)
                and isinstance(atom.left, exp.Column)
                and isinstance(atom.right, exp.Column)
            ):
                left, right = self.resolve(atom.left), self.resolve(atom.right)
                if left and right:
                    pairs.append((left, right))
        return pairs

    def _check_joins(self) -> None:
        order = [a for a in self.scope.sources]
        joins = self.select.args.get("joins") or []
        seen = order[:1]
        for join, alias in zip(joins, order[1:], strict=False):
            on, using = join.args.get("on"), join.args.get("using")
            if str(join.args.get("kind") or "").upper() == "CROSS" or (not on and not using):
                self.run.fail(
                    "CROSS_JOIN", "Every join needs a join condition; cross joins are not allowed."
                )
                seen.append(alias)
                continue
            if alias in self.derived:
                seen.append(alias)
                continue
            pairs = self._equalities(on)
            if using:
                names = [u.name for u in using]
                prior = [
                    a
                    for a in seen
                    if a in self.stores and all(self.stores[a].column(n) for n in names)
                ]
                if prior and alias in self.stores:
                    pairs += [
                        (
                            _Ref(prior[0], self.stores[prior[0]], n),
                            _Ref(alias, self.stores[alias], n),
                        )
                        for n in names
                    ]
            if not self._declare(alias, pairs, seen):
                self.run.fail(
                    "UNDECLARED_JOIN",
                    f"The join to {self.stores[alias].store} does not follow a relationship "
                    "declared in the catalogue.",
                )
            seen.append(alias)

    def _declare(self, alias: str, pairs: list[tuple[_Ref, _Ref]], seen: list[str]) -> bool:
        mine = self.stores.get(alias)
        if mine is None:
            return True
        for prior in seen:
            other = self.stores.get(prior)
            if other is None:
                continue
            between = {
                (a.column, b.column) if a.alias == prior else (b.column, a.column)
                for a, b in pairs
                if {a.alias, b.alias} == {prior, alias}
            }
            for source_alias, source, target in ((prior, other, mine), (alias, mine, other)):
                relationship = self.run.catalogue.relationship(source.store, target.store)
                if relationship is None:
                    continue
                wanted = (
                    set(relationship.join_on)
                    if source_alias == prior
                    else {(b, a) for a, b in relationship.join_on}
                )
                if wanted and wanted <= between:
                    cardinality = (
                        relationship.cardinality
                        if source_alias == prior
                        else {"one_to_many": "many_to_one", "many_to_one": "one_to_many"}.get(
                            relationship.cardinality, relationship.cardinality
                        )
                    )
                    self.joined.append((prior, alias, cardinality))
                    inverse = {"one_to_many": "many_to_one", "many_to_one": "one_to_many"}.get(
                        cardinality, cardinality
                    )
                    self.joined.append((alias, prior, inverse))
                    return True
        return False

    # -- mandatory filters -----------------------------------------------------------------------

    def _atom_matches(self, atom: exp.Expr, alias: str, column: str) -> tuple[str, Any] | None:
        """Describe an atom that constrains alias.column: ('true',) / ('eq', v) / ('in', [v])."""
        if isinstance(atom, exp.Column):
            ref = self.resolve(atom)
            return ("true", True) if ref and (ref.alias, ref.column) == (alias, column) else None
        if isinstance(atom, exp.EQ | exp.NEQ):
            for col, other in ((atom.left, atom.right), (atom.right, atom.left)):
                if isinstance(col, exp.Column):
                    ref = self.resolve(col)
                    known, value = self.run.value_of(other)
                    if ref and known and (ref.alias, ref.column) == (alias, column):
                        return ("eq" if isinstance(atom, exp.EQ) else "neq", value)
        if isinstance(atom, exp.In) and isinstance(atom.this, exp.Column):
            ref = self.resolve(atom.this)
            if ref and (ref.alias, ref.column) == (alias, column):
                values = [self.run.value_of(e) for e in atom.expressions]
                if all(k for k, _ in values):
                    return ("in", [v for _, v in values])
        return None

    def _check_required_filters(self) -> None:
        for alias, store in self.stores.items():
            for rule in store.required_filters:
                if (rule.waived_by == "history" and self.run.options.history_requested) or (
                    rule.waived_by == "cancelled" and self.run.options.include_cancelled
                ):
                    continue
                if not any(self._satisfies(a, alias, rule.column, rule.value) for a in self.atoms):
                    code = "CURRENT_VERSION" if rule.waived_by == "history" else "STATUS_FILTER"
                    self.run.fail(
                        code,
                        f"{store.store} must be filtered to {rule.column} = {rule.value} "
                        "unless the question asks otherwise.",
                    )

    def _satisfies(self, atom: exp.Expr, alias: str, column: str, wanted: Any) -> bool:
        found = self._atom_matches(atom, alias, column)
        if found is None:
            return False
        kind, value = found
        if wanted is True:
            return kind == "true" or (kind == "eq" and value in (True, "true"))
        return (kind == "eq" and value == wanted) or (kind == "in" and value == [wanted])

    # -- aggregates: fan-out and currency --------------------------------------------------------

    def _own_aggregates(self) -> list[exp.Expr]:
        found: list[exp.Expr] = []
        for node in self.select.find_all(exp.Sum, exp.Avg, exp.Min, exp.Max, exp.Count):
            if node.find_ancestor(exp.Select) is self.select:
                found.append(node)
        return found

    def _group_columns(self) -> set[tuple[str, str]]:
        group = self.select.args.get("group")
        columns: set[tuple[str, str]] = set()
        for item in group.expressions if group else []:
            target = item
            if isinstance(item, exp.Literal) and not item.is_string:
                position = int(item.name) - 1
                selects = self.select.selects
                target = selects[position].unalias() if 0 <= position < len(selects) else item
            for col in target.find_all(exp.Column):
                ref = self.resolve(col)
                if ref:
                    columns.add((ref.alias, ref.column))
        return columns

    def _pinned(self, alias: str, columns: list[str]) -> bool:
        for atom in self.atoms:
            for column in columns:
                found = self._atom_matches(atom, alias, column)
                if found and ((found[0] == "eq") or (found[0] == "in" and len(found[1]) == 1)):
                    return True
        return False

    def _check_aggregates(self) -> None:
        groups = self._group_columns()
        for node in self._own_aggregates():
            columns = [c for c in node.find_all(exp.Column)]
            is_distinct_count = isinstance(node, exp.Count) and isinstance(node.this, exp.Distinct)
            for column in columns:
                ref = self.resolve(column)
                if ref is None:
                    continue
                if not is_distinct_count and any(
                    a == ref.alias and c == "one_to_many" for a, _b, c in self.joined
                ):
                    self.run.fail(
                        "FAN_OUT",
                        f"Aggregating {ref.store.store}.{ref.column} after a join to a one-to-many "
                        "store repeats its rows. Aggregate before joining, or count distinct keys.",
                    )
                money = ref.store.money_field(ref.column)
                if money is None or isinstance(node, exp.Count):
                    continue
                window = node.find_ancestor(exp.Window)
                split = {c for a, c in self._partition_or_group(window, groups) if a == ref.alias}
                if (
                    money.currency_column in split
                    or any(p in split for p in money.pinned_by)
                    or self._pinned(ref.alias, [money.currency_column, *money.pinned_by])
                ):
                    continue
                self.run.fail(
                    "CURRENCY_MIX",
                    f"{ref.store.store}.{ref.column} is money in several currencies. Group by "
                    f"{money.currency_column}, or filter to one currency or account.",
                )

    def _partition_or_group(
        self, window: exp.Expr | None, groups: set[tuple[str, str]]
    ) -> set[tuple[str, str]]:
        if window is None:
            return groups
        result: set[tuple[str, str]] = set()
        for part in window.args.get("partition_by") or []:
            for col in part.find_all(exp.Column):
                ref = self.resolve(col)
                if ref:
                    result.add((ref.alias, ref.column))
        return result

    # -- dates -----------------------------------------------------------------------------------

    def _date_ref(self, node: exp.Expr | None) -> _Ref | None:
        if isinstance(node, exp.Column):
            ref = self.resolve(node)
            if ref and self._is_date(ref):
                return ref
        return None

    def _check_dates(self) -> None:
        direct = {exp.GT: "GT", exp.GTE: "GTE", exp.LT: "LT", exp.LTE: "LTE"}
        mirrored = {"GT": "LT", "GTE": "LTE", "LT": "GT", "LTE": "GTE"}
        bounds: dict[tuple[str, str], list[str]] = {}
        touched: set[tuple[str, str]] = set()
        for atom in self.atoms:
            if isinstance(atom, exp.Between):
                ref = self._date_ref(atom.this)
                if ref:
                    touched.add((ref.alias, ref.column))
                    self.run.fail(
                        "DATE_INTERVAL",
                        f"BETWEEN on {ref.column} is closed at both ends; use >= start and < end.",
                    )
                continue
            if isinstance(atom, exp.EQ):
                ref = self._date_ref(atom.left) or self._date_ref(atom.right)
                if ref:
                    touched.add((ref.alias, ref.column))
                continue
            if isinstance(atom, exp.In):
                ref = self._date_ref(atom.this)
                if ref:
                    touched.add((ref.alias, ref.column))
                continue
            name = next((v for k, v in direct.items() if isinstance(atom, k)), None)
            if name is None or not isinstance(atom, exp.Binary):
                continue
            for side, other, op in (
                (atom.left, atom.right, name),
                (atom.right, atom.left, mirrored[name]),
            ):
                ref = self._date_ref(side)
                if ref and self.run.value_of(other)[0]:
                    bounds.setdefault((ref.alias, ref.column), []).append(op)
                    touched.add((ref.alias, ref.column))
        for (_alias, column), ops in bounds.items():
            lowers = [o for o in ops if o in ("GT", "GTE")]
            uppers = [o for o in ops if o in ("LT", "LTE")]
            if lowers and uppers and ("GT" in lowers or "LTE" in uppers):
                self.run.fail(
                    "DATE_INTERVAL",
                    f"The window on {column} must be half-open: "
                    f"{column} >= start AND {column} < end.",
                )
        wanted = self.run.options.date_field
        if wanted:
            for alias, column in touched:
                dates = self.stores[alias].dates
                if wanted in dates and column in dates and column != wanted:
                    self.run.fail(
                        "DATE_FIELD",
                        f"The question asks for {wanted}, but the query filters on {column}.",
                    )

    def _is_date(self, ref: _Ref) -> bool:
        column = ref.store.column(ref.column)
        return column is not None and column.type in ("date", "timestamp with time zone")
