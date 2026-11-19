"""One-shot setup for the laptop profile: seed the warehouse and create every database role.

Idempotent: it reloads the synthetic world (seed 7), re-provisions per-user roles from
identity/users.json, and creates the ledger and drafts tables without dropping history.
Runs as a compose job with the owner credentials; no service holds them.
"""

import os
from pathlib import Path

from copilot_drafts import ensure_schema as ensure_drafts
from copilot_memory import ensure_schema as ensure_memory
from halcyon_world import generate_world
from halcyon_world.load import load_world
from policy_gateway.postgres_ledger import ensure_schema as ensure_ledger
from query_api.provision import load_identity, provision

IDENTITY = Path(os.environ.get("IDENTITY_FILE", "/app/deploy/compose/identity/users.json"))


def main() -> None:
    host = os.environ.get("POSTGRES_HOST", "postgres:5432")
    owner = f"postgresql://copilot_owner:{os.environ['POSTGRES_OWNER_PASSWORD']}@{host}/analytics"
    load_world(generate_world(seed=7), owner)
    provision(owner, load_identity(IDENTITY), login_password=os.environ["QUERY_DB_PASSWORD"])
    ensure_ledger(owner, app_password=os.environ["GATEWAY_DB_PASSWORD"])
    ensure_drafts(
        owner,
        drafts_password=os.environ["DRAFTS_DB_PASSWORD"],
        approval_password=os.environ["APPROVAL_DB_PASSWORD"],
    )
    ensure_memory(owner, memory_password=os.environ["MEMORY_DB_PASSWORD"])
    print("bootstrap complete")


if __name__ == "__main__":
    main()
