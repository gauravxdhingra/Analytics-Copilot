"""The memory schema. Row-level security lets the database decide whose rows are whose."""

import psycopg
from psycopg import sql

MEMORY_ROLE = "copilot_memory"

_DDL = """
CREATE SCHEMA IF NOT EXISTS memory;

CREATE TABLE IF NOT EXISTS memory.turns (
    turn_id         bigserial PRIMARY KEY,
    tenant_id       text        NOT NULL,
    user_id         text        NOT NULL,
    conversation_id text        NOT NULL,
    question        text        NOT NULL CHECK (length(question) <= 2000),
    status          text        NOT NULL CHECK (length(status) <= 20),
    plan            jsonb,
    summary         text        NOT NULL CHECK (length(summary) <= 500),
    trace_id        text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    expires_at      timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS turns_by_owner
    ON memory.turns (tenant_id, user_id, conversation_id, turn_id);

CREATE TABLE IF NOT EXISTS memory.preferences (
    pref_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  text        NOT NULL,
    user_id    text        NOT NULL,
    kind       text        NOT NULL CHECK (kind IN ('preference', 'saved_definition')),
    name       text,
    text       text        NOT NULL CHECK (length(text) <= 300),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((kind = 'saved_definition') = (name IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS definitions_by_name
    ON memory.preferences (tenant_id, user_id, name) WHERE kind = 'saved_definition';

ALTER TABLE memory.turns ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory.turns FORCE ROW LEVEL SECURITY;
ALTER TABLE memory.preferences ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory.preferences FORCE ROW LEVEL SECURITY;

-- A session names its owner with set_config('memory.*', ..., true). Unnamed means no rows.
DROP POLICY IF EXISTS own_turns ON memory.turns;
CREATE POLICY own_turns ON memory.turns
    USING (tenant_id = current_setting('memory.tenant_id', true)
       AND user_id = current_setting('memory.user_id', true))
    WITH CHECK (tenant_id = current_setting('memory.tenant_id', true)
       AND user_id = current_setting('memory.user_id', true));
DROP POLICY IF EXISTS own_preferences ON memory.preferences;
CREATE POLICY own_preferences ON memory.preferences
    USING (tenant_id = current_setting('memory.tenant_id', true)
       AND user_id = current_setting('memory.user_id', true))
    WITH CHECK (tenant_id = current_setting('memory.tenant_id', true)
       AND user_id = current_setting('memory.user_id', true));
"""


def ensure_schema(owner_dsn: str, *, memory_password: str) -> None:
    """Create the tables, the policies and the service role. Safe to run again."""
    with psycopg.connect(owner_dsn) as conn:
        conn.execute(_DDL)
        exists = conn.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (MEMORY_ROLE,)
        ).fetchone()
        verb = "ALTER" if exists else "CREATE"
        conn.execute(
            sql.SQL(
                verb + " ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOINHERIT "
                "PASSWORD {}"
            ).format(sql.Identifier(MEMORY_ROLE), sql.Literal(memory_password))
        )
        role = sql.Identifier(MEMORY_ROLE)
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA memory TO {}").format(role))
        # The Query API's hardening revokes set_config from everyone so a user's SQL cannot
        # change session settings. This service role needs it to name its owner to the policy.
        conn.execute(
            sql.SQL(
                "GRANT EXECUTE ON FUNCTION pg_catalog.set_config(text, text, boolean) TO {}"
            ).format(role)
        )
        for table in ("turns", "preferences"):
            conn.execute(
                sql.SQL("REVOKE ALL ON memory.{} FROM {}").format(sql.Identifier(table), role)
            )
        # Turns are written once and expire; they are never edited.
        conn.execute(sql.SQL("GRANT SELECT, INSERT, DELETE ON memory.turns TO {}").format(role))
        conn.execute(
            sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON memory.preferences TO {}").format(role)
        )
        conn.execute(sql.SQL("GRANT USAGE ON SEQUENCE memory.turns_turn_id_seq TO {}").format(role))
