"""Schema-drift check: the catalogue and the warehouse must describe the same tables."""

from typing import Any

from copilot_catalogue.catalogue import Catalogue


def check_drift(catalogue: Catalogue, schema: dict[str, Any]) -> list[str]:
    """Compare against the Query API's `/v1/schema`. Returns human-readable issues."""
    issues: list[str] = []
    warehouse = {
        f"{t['schema']}.{t['table']}": {c["name"]: c["type"] for c in t["columns"]}
        for t in schema["tables"]
    }
    for name in sorted(set(warehouse) - set(catalogue.stores)):
        issues.append(f"undocumented table {name}")
    for name, store in sorted(catalogue.stores.items()):
        actual = warehouse.get(name)
        if actual is None:
            issues.append(f"table {name} removed from the warehouse")
            continue
        documented = {c.name: c.type for c in store.columns}
        for column in sorted(set(actual) - set(documented)):
            issues.append(f"undocumented column {name}.{column}")
        for column in sorted(set(documented) - set(actual)):
            issues.append(f"column {name}.{column} removed from the warehouse")
        for column in sorted(set(actual) & set(documented)):
            if actual[column] != documented[column]:
                issues.append(
                    f"type of {name}.{column} changed: catalogue {documented[column]}, "
                    f"warehouse {actual[column]}"
                )
    return issues
