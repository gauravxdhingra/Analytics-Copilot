"""The guarantees that must hold even if a service is buggy or compromised."""

import psycopg
import pytest

pytestmark = pytest.mark.integration

PAYLOAD = '{"summary": "s", "recommended_actions": ["review"], "evidence": []}'


def _insert(conn: psycopg.Connection, *, status: str = "draft", created_by: str = "maker-1") -> str:
    row = conn.execute(
        "INSERT INTO ops.drafts (kind, entity_id, title, payload, status, created_by) "
        "VALUES ('case_file', 'HAL-DE', 'Review', %s::jsonb, %s, %s) RETURNING draft_id",
        (PAYLOAD, status, created_by),
    ).fetchone()
    assert row is not None
    return str(row[0])


def _submit(conn: psycopg.Connection, draft_id: str, by: str = "maker-1") -> None:
    conn.execute(
        "UPDATE ops.drafts SET status = 'submitted', submitted_by = %s, submitted_at = now() "
        "WHERE draft_id = %s",
        (by, draft_id),
    )


def _decide(conn: psycopg.Connection, draft_id: str, status: str, by: str) -> None:
    conn.execute(
        "UPDATE ops.drafts SET status = %s, decided_by = %s, decided_at = now() "
        "WHERE draft_id = %s",
        (status, by, draft_id),
    )


# --- the drafts role: create and submit, never decide ---------------------------------------------


def test_the_drafts_role_can_create_and_submit(drafts_dsn: str) -> None:
    with psycopg.connect(drafts_dsn, autocommit=True) as conn:
        draft = _insert(conn)
        _submit(conn, draft)
        row = conn.execute("SELECT status FROM ops.drafts WHERE draft_id = %s", (draft,)).fetchone()
        assert row == ("submitted",)


@pytest.mark.parametrize("status", ["submitted", "approved", "rejected"])
def test_a_draft_can_only_be_created_in_the_draft_state(drafts_dsn: str, status: str) -> None:
    with (
        psycopg.connect(drafts_dsn, autocommit=True) as conn,
        pytest.raises(psycopg.errors.RaiseException),
    ):
        _insert(conn, status=status)


@pytest.mark.parametrize("final", ["approved", "rejected"])
def test_the_drafts_role_cannot_approve_or_reject(drafts_dsn: str, final: str) -> None:
    with psycopg.connect(drafts_dsn, autocommit=True) as conn:
        draft = _insert(conn)
        _submit(conn, draft)
        with pytest.raises((psycopg.errors.RaiseException, psycopg.errors.InsufficientPrivilege)):
            _decide(conn, draft, final, "someone-else")


def test_the_drafts_role_cannot_edit_the_content_after_creation(drafts_dsn: str) -> None:
    with psycopg.connect(drafts_dsn, autocommit=True) as conn:
        draft = _insert(conn)
        with pytest.raises((psycopg.errors.RaiseException, psycopg.errors.InsufficientPrivilege)):
            conn.execute("UPDATE ops.drafts SET title = 'changed' WHERE draft_id = %s", (draft,))
        with pytest.raises((psycopg.errors.RaiseException, psycopg.errors.InsufficientPrivilege)):
            conn.execute(
                "UPDATE ops.drafts SET payload = '{}'::jsonb WHERE draft_id = %s", (draft,)
            )


def test_nothing_can_be_deleted(drafts_dsn: str, approval_dsn: str) -> None:
    with psycopg.connect(drafts_dsn, autocommit=True) as conn:
        draft = _insert(conn)
        with pytest.raises((psycopg.errors.RaiseException, psycopg.errors.InsufficientPrivilege)):
            conn.execute("DELETE FROM ops.drafts WHERE draft_id = %s", (draft,))
    with (
        psycopg.connect(approval_dsn, autocommit=True) as conn,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        conn.execute("DELETE FROM ops.drafts")


# --- the approval role: decide submitted drafts, nothing else ------------------------------------------


def test_the_approval_role_can_approve_a_submitted_draft(
    drafts_dsn: str, approval_dsn: str
) -> None:
    with psycopg.connect(drafts_dsn, autocommit=True) as conn:
        draft = _insert(conn)
        _submit(conn, draft)
    with psycopg.connect(approval_dsn, autocommit=True) as conn:
        _decide(conn, draft, "approved", "approver-1")
        row = conn.execute(
            "SELECT status, decided_by FROM ops.drafts WHERE draft_id = %s", (draft,)
        ).fetchone()
        assert row == ("approved", "approver-1")


def test_the_approval_role_cannot_decide_a_draft_that_was_never_submitted(
    drafts_dsn: str, approval_dsn: str
) -> None:
    with psycopg.connect(drafts_dsn, autocommit=True) as conn:
        draft = _insert(conn)
    with (
        psycopg.connect(approval_dsn, autocommit=True) as conn,
        pytest.raises(psycopg.errors.RaiseException),
    ):
        _decide(conn, draft, "approved", "approver-1")


def test_a_decision_is_final(drafts_dsn: str, approval_dsn: str) -> None:
    with psycopg.connect(drafts_dsn, autocommit=True) as conn:
        draft = _insert(conn)
        _submit(conn, draft)
    with psycopg.connect(approval_dsn, autocommit=True) as conn:
        _decide(conn, draft, "approved", "approver-1")
        with pytest.raises(psycopg.errors.RaiseException):
            _decide(conn, draft, "rejected", "approver-2")


def test_the_approval_role_cannot_create_or_edit_drafts(drafts_dsn: str, approval_dsn: str) -> None:
    with psycopg.connect(drafts_dsn, autocommit=True) as conn:
        draft = _insert(conn)
        _submit(conn, draft)
    with psycopg.connect(approval_dsn, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _insert(conn)
        with pytest.raises((psycopg.errors.InsufficientPrivilege, psycopg.errors.RaiseException)):
            conn.execute("UPDATE ops.drafts SET title = 'x' WHERE draft_id = %s", (draft,))


# --- separation of duties, in the database -------------------------------------------------------------


def test_the_maker_cannot_decide_their_own_draft(drafts_dsn: str, approval_dsn: str) -> None:
    with psycopg.connect(drafts_dsn, autocommit=True) as conn:
        draft = _insert(conn, created_by="maker-1")
        _submit(conn, draft, by="maker-1")
    with (
        psycopg.connect(approval_dsn, autocommit=True) as conn,
        pytest.raises(psycopg.errors.CheckViolation),
    ):
        _decide(conn, draft, "approved", "maker-1")


def test_the_submitter_cannot_decide_what_they_submitted(
    drafts_dsn: str, approval_dsn: str
) -> None:
    with psycopg.connect(drafts_dsn, autocommit=True) as conn:
        draft = _insert(conn, created_by="maker-1")
        _submit(conn, draft, by="submitter-2")
    with (
        psycopg.connect(approval_dsn, autocommit=True) as conn,
        pytest.raises(psycopg.errors.CheckViolation),
    ):
        _decide(conn, draft, "approved", "submitter-2")


# --- even the owner cannot forge an approval ---------------------------------------------------------------


def test_the_database_owner_cannot_approve_either(
    drafts_dsn: str, owner: psycopg.Connection
) -> None:
    with psycopg.connect(drafts_dsn, autocommit=True) as conn:
        draft = _insert(conn)
        _submit(conn, draft)
    with pytest.raises(psycopg.errors.RaiseException):
        _decide(owner, draft, "approved", "approver-1")


def test_the_service_roles_see_nothing_else_in_the_database(
    drafts_dsn: str, approval_dsn: str
) -> None:
    for dsn in (drafts_dsn, approval_dsn):
        with psycopg.connect(dsn, autocommit=True) as conn:
            for table in ("treasury.transactions", "eval.labels", "docs.documents"):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    conn.execute(f"SELECT 1 FROM {table} LIMIT 1")
