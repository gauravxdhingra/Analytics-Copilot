"""Check that docs/adr/README.md lists every ADR with the status the file declares."""

import re
import sys
from collections import Counter
from pathlib import Path

_FILE = re.compile(r"^(\d{4})-.+\.md$")
_ROW = re.compile(r"^\|\s*\[(\d{4})\]\(([^)]+)\)\s*\|[^|]*\|\s*([^|]+?)\s*\|")
_STATUS = re.compile(r"^- \*\*Status:\*\*\s*(.+?)\s*$", re.MULTILINE)


def check(adr_dir: Path) -> list[str]:
    """Return a list of problems; empty means the index is consistent."""
    errors: list[str] = []
    files = {p.name: p for p in sorted(adr_dir.glob("*.md")) if _FILE.match(p.name)}
    adrs = {n: p for n, p in files.items() if not n.startswith("0000-")}

    numbers = Counter(n[:4] for n in adrs)
    errors += [f"duplicate ADR number {num}" for num, count in numbers.items() if count > 1]

    rows: dict[str, str] = {}
    for line in (adr_dir / "README.md").read_text().splitlines():
        match = _ROW.match(line)
        if match:
            rows[match.group(2)] = match.group(3)

    for name in rows:
        if name not in adrs:
            errors.append(f"README lists {name}: no such file")
    for name, path in adrs.items():
        if name not in rows:
            errors.append(f"{name} is not listed in README")
            continue
        declared = _STATUS.search(path.read_text())
        if declared is None:
            errors.append(f"{name} has no Status line")
        elif declared.group(1) != rows[name]:
            errors.append(f"{name}: status {declared.group(1)!r} but README says {rows[name]!r}")
    return errors


def main() -> int:
    errors = check(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("docs/adr"))
    for error in errors:
        sys.stderr.write(f"ADR index: {error}\n")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
