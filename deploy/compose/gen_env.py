"""Create deploy/compose/.env with fresh random secrets: `make env`.

Never overwrites an existing .env, so running it twice is safe. The file is git-ignored.
"""

import os
import secrets
import sys
from pathlib import Path

HERE = Path(__file__).parent
EXAMPLE = HERE / ".env.example"
TARGET = HERE / ".env"


def main() -> int:
    if TARGET.exists():
        sys.stdout.write(f"{TARGET} already exists; leaving it alone.\n")
        return 0
    lines: list[str] = []
    for line in EXAMPLE.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.startswith("#"):
            value = secrets.token_urlsafe(24) if "change-me" in value else value
            line = f"{key}={value}"
        lines.append(line)
    # Created owner-only from the first byte: no window where the secrets are world-readable.
    fd = os.open(TARGET, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write("\n".join(lines) + "\n")
    sys.stdout.write(f"wrote {TARGET} with random secrets (mode 600).\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
