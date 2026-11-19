"""Join-graph reasoning shared by the plan renderer and the SQL verifier."""

# A join that can match one row to several rows repeats the rows on the other side.
MULTIPLYING = frozenset({"one_to_many", "many_to_many"})

INVERSE = {
    "one_to_many": "many_to_one",
    "many_to_one": "one_to_many",
    "one_to_one": "one_to_one",
    "many_to_many": "many_to_many",
}


def repeats_rows(alias: str, joins: list[tuple[str, str, str]]) -> bool:
    """True if the joins can repeat `alias`'s rows, at any distance.

    `joins` holds (from, to, cardinality from->to) in both directions. Walk outward from the
    table being aggregated: a many-to-one hop keeps its rows, but any one-to-many or many-to-many
    hop further along repeats them, so summing or counting them gives a wrong total.
    """
    seen = {alias}
    frontier = [alias]
    while frontier:
        here = frontier.pop()
        for source, target, cardinality in joins:
            if source != here or target in seen:
                continue
            if cardinality in MULTIPLYING:
                return True
            seen.add(target)
            frontier.append(target)
    return False
