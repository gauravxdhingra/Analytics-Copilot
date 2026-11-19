"""Create or complete deploy/compose/.env with random secrets: `make env`.

Never changes a value that is already there. A fresh file is built from .env.example; an existing
one only gains variables that compose.yaml requires (`${NAME:?}`) and the file lacks, so
`make up` cannot fail on a missing secret after an upgrade. The file is git-ignored.
"""

import os
import re
import secrets
import sys
from pathlib import Path

HERE = Path(__file__).parent
EXAMPLE = HERE / ".env.example"
COMPOSE = HERE / "compose.yaml"
TARGET = HERE / ".env"

_REQUIRED = re.compile(r"\$\{([A-Z][A-Z0-9_]*):\?")


def _fresh_lines(example: Path) -> list[str]:
    lines: list[str] = []
    for line in example.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.startswith("#"):
            value = secrets.token_urlsafe(24) if "change-me" in value else value
            line = f"{key}={value}"
        lines.append(line)
    return lines


def _present(text: str) -> set[str]:
    keys = (line.partition("=") for line in text.splitlines())
    return {k.strip() for k, sep, _ in keys if sep and not k.lstrip().startswith("#")}


def sync(example: Path, compose: Path, target: Path) -> list[str]:
    """Make `target` complete. Returns the names of the variables it had to add."""
    required = list(dict.fromkeys(_REQUIRED.findall(compose.read_text())))
    added: list[str] = []
    if not target.exists():
        body = "\n".join(_fresh_lines(example)) + "\n"
        # Created owner-only from the first byte: no window where the secrets are world-readable.
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(body)
        added = [name for name in required if name not in _present(body)]
    else:
        added = [name for name in required if name not in _present(target.read_text())]
    if added and target.exists():
        needs_newline = not target.read_text().endswith("\n") if target.stat().st_size else False
        with target.open("a") as handle:  # appending keeps the file's permissions
            if needs_newline:
                handle.write("\n")
            for name in added:
                handle.write(f"{name}={secrets.token_urlsafe(24)}\n")
    return added


def main(example: Path = EXAMPLE, compose: Path = COMPOSE, target: Path = TARGET) -> int:
    existed = target.exists()
    added = sync(example, compose, target)
    if not existed:
        sys.stdout.write(f"wrote {target} with random secrets (mode 600).\n")
    elif added:
        sys.stdout.write(f"added to {target} (existing values untouched): {', '.join(added)}\n")
    else:
        sys.stdout.write(f"{target} is complete; leaving it alone.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
