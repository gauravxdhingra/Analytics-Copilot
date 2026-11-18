"""The drafts table and its guards. Maker-checker is enforced here, not only in services."""

import psycopg
from psycopg import sql

DRAFTS_ROLE = "copilot_drafts"
APPROVAL_ROLE = "copilot_approval"

_DDL = """
CREATE SCHEMA IF NOT EXISTS ops;
CREATE TABLE IF NOT EXISTS ops.drafts (
    draft_id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind             text NOT NULL CHECK (kind IN ('case_file')),
    entity_id        text NOT NULL,
    title            text NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
    payload          jsonb NOT NULL,
    status           text NOT NULL DEFAULT 'draft'
                     CHECK (status IN ('draft', 'submitted', 'approved', 'rejected')),
    created_by       text NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    submitted_by     text,
    submitted_at     timestamptz,
    decided_by       text,
    decided_at       timestamptz,
    decision_comment text CHECK (decision_comment IS NULL OR length(decision_comment) <= 1000),
    trace_id         text,
    -- Nobody who made or submitted a draft can decide it.
    CHECK (decided_by IS NULL OR
           (decided_by <> created_by AND decided_by IS DISTINCT FROM submitted_by)),
    -- A decision exists exactly when the draft is approved or rejected.
    CHECK ((status IN ('approved', 'rejected')) = (decided_by IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS drafts_status ON ops.drafts (status, entity_id);

CREATE OR REPLACE FUNCTION ops.drafts_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'drafts cannot be deleted';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'draft' THEN
            RAISE EXCEPTION 'drafts are created in the draft state';
        END IF;
        RETURN NEW;
    END IF;
    -- UPDATE: content is immutable; only the allowed role may take each transition.
    IF (NEW.draft_id, NEW.kind, NEW.entity_id, NEW.title, NEW.payload,
        NEW.created_by, NEW.created_at, NEW.trace_id)
       IS DISTINCT FROM
       (OLD.draft_id, OLD.kind, OLD.entity_id, OLD.title, OLD.payload,
        OLD.created_by, OLD.created_at, OLD.trace_id) THEN
        RAISE EXCEPTION 'draft content is immutable';
    END IF;
    IF OLD.status = 'draft' AND NEW.status = 'submitted' THEN
        IF current_user <> 'copilot_drafts' THEN
            RAISE EXCEPTION 'only the drafts service may submit a draft';
        END IF;
    ELSIF OLD.status = 'submitted' AND NEW.status IN ('approved', 'rejected') THEN
        IF current_user <> 'copilot_approval' THEN
            RAISE EXCEPTION 'only the approval service may decide a draft';
        END IF;
    ELSE
        RAISE EXCEPTION 'illegal transition from % to %', OLD.status, NEW.status;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS drafts_guard ON ops.drafts;
CREATE TRIGGER drafts_guard BEFORE INSERT OR UPDATE OR DELETE ON ops.drafts
    FOR EACH ROW EXECUTE FUNCTION ops.drafts_guard();
"""


def _role(conn: psycopg.Connection, name: str, password: str) -> None:
    exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,)).fetchone()
    verb = "ALTER" if exists else "CREATE"
    conn.execute(
        sql.SQL(
            verb + " ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD {}"
        ).format(sql.Identifier(name), sql.Literal(password))
    )


def ensure_schema(
    owner_dsn: str, *, drafts_password: str, approval_password: str, reset: bool = False
) -> None:
    """Create the table, the guard trigger and the two service roles. `reset` is for tests."""
    with psycopg.connect(owner_dsn) as conn:
        if reset:
            conn.execute("DROP TABLE IF EXISTS ops.drafts")
        conn.execute(_DDL)
        _role(conn, DRAFTS_ROLE, drafts_password)
        _role(conn, APPROVAL_ROLE, approval_password)
        for role in (DRAFTS_ROLE, APPROVAL_ROLE):
            ident = sql.Identifier(role)
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA ops TO {}").format(ident))
            conn.execute(sql.SQL("REVOKE ALL ON ops.drafts FROM {}").format(ident))
            conn.execute(sql.SQL("GRANT SELECT ON ops.drafts TO {}").format(ident))
        # Column-level grants narrow each role further than the trigger does.
        conn.execute(
            sql.SQL("GRANT INSERT ON ops.drafts TO {}").format(sql.Identifier(DRAFTS_ROLE))
        )
        conn.execute(
            sql.SQL("GRANT UPDATE (status, submitted_by, submitted_at) ON ops.drafts TO {}").format(
                sql.Identifier(DRAFTS_ROLE)
            )
        )
        conn.execute(
            sql.SQL(
                "GRANT UPDATE (status, decided_by, decided_at, decision_comment) "
                "ON ops.drafts TO {}"
            ).format(sql.Identifier(APPROVAL_ROLE))
        )
