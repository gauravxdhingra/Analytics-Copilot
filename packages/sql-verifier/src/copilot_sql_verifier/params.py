"""Turn the literals in a SQL string into bound parameters."""

from typing import Any

import sqlglot
from sqlglot import exp

_MAX_STRUCTURAL_INT = 100


def _is_structural(literal: exp.Literal) -> bool:
    if isinstance(literal.parent, exp.Parameter | exp.Limit | exp.Offset | exp.Fetch):
        return True
    if literal.is_string:
        return False
    try:
        number = float(literal.name)
    except ValueError:
        return False
    return number.is_integer() and abs(number) <= _MAX_STRUCTURAL_INT


def parameterize_literals(sql: str) -> tuple[str, list[Any]]:
    """Replace string and non-trivial numeric literals with `$n`, returning the bound values.

    Used to compare hand-written reference SQL with what the verifier accepts, and by tools
    that want to run literal-bearing SQL through the parameterised path.
    """
    params: list[Any] = []

    def visit(node: exp.Expr) -> exp.Expr:
        if isinstance(node, exp.Literal) and not _is_structural(node):
            if node.is_string:
                params.append(node.name)
            else:
                value = float(node.name)
                params.append(int(value) if value.is_integer() else node.name)
            return exp.Parameter(this=exp.Literal.number(len(params)))
        return node

    tree = sqlglot.parse_one(sql, dialect="postgres").transform(visit)
    return tree.sql(dialect="postgres"), params
