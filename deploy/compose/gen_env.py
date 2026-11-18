"""Create deploy/compose/.env with fresh random secrets: `make env`.

Never overwrites an existing .env, so running it twice is safe. The file is git-ignored.
"""

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
    TARGET.write_text("\n".join(lines) + "\n")
    TARGET.chmod(0o600)  # owner-only
    sys.stdout.write(f"wrote {TARGET} with random secrets (mode 600).\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
