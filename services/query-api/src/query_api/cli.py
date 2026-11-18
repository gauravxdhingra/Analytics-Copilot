"""Provision database roles and row-level security: `uv run provision-query-api`."""

import os
import sys
from pathlib import Path

from query_api.provision import load_identity, provision


def main() -> int:
    host = os.environ.get("POSTGRES_HOST", "localhost:5433")
    owner = f"postgresql://copilot_owner:{os.environ['POSTGRES_OWNER_PASSWORD']}@{host}/analytics"
    identity = Path(os.environ.get("IDENTITY_FILE", "deploy/compose/identity/users.json"))
    users = load_identity(identity)
    provision(owner, users, login_password=os.environ["QUERY_DB_PASSWORD"])
    sys.stdout.write(f"provisioned {len(users)} users\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
