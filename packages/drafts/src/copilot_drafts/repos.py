"""Repositories for the two service roles. Each can only do what its database role allows."""

from typing import Any, Literal

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from query_api.provision import entity_role, user_role

from copilot_drafts.models import Draft, DraftPayload, DraftStatus

_COLUMNS = (
    "draft_id::text, kind, entity_id, title, payload, status, created_by, created_at, "
    "submitted_by, submitted_at, decided_by, decided_at, decision_comment, trace_id"
)


class DraftError(Exception):
    """A mapped failure code. Raw database text never leaves this module."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _draft(row: dict[str, Any]) -> Draft:
    return Draft.model_validate({**row, "payload": DraftPayload.model_validate(row["payload"])})


def _map(exc: psycopg.Error) -> DraftError:
    if isinstance(exc, psycopg.errors.CheckViolation):
        return DraftError("SOD_VIOLATION")  # the maker-checker constraint
    if isinstance(exc, psycopg.errors.RaiseException):
        return DraftError("ILLEGAL_TRANSITION")  # the guard trigger
    if isinstance(exc, psycopg.errors.InsufficientPrivilege):
        return DraftError("FORBIDDEN")
    return DraftError("UNAVAILABLE")


class DraftsRepo:
    """Used by the drafts MCP server: create, read and submit. It cannot approve."""

    def __init__(self, dsn: str, *, connect_timeout: int = 5) -> None:
        self._dsn = dsn
        self._timeout = connect_timeout

    def _connect(self) -> psycopg.Connection[dict[str, Any]]:
        return psycopg.connect(self._dsn, connect_timeout=self._timeout, row_factory=dict_row)

    def create(
        self,
        *,
        kind: Literal["case_file"],
        entity_id: str,
        title: str,
        payload: DraftPayload,
        created_by: str,
        trace_id: str | None,
    ) -> Draft:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "INSERT INTO ops.drafts "
                    "(kind, entity_id, title, payload, created_by, trace_id) "
                    f"VALUES (%s, %s, %s, %s, %s, %s) RETURNING {_COLUMNS}",
                    (kind, entity_id, title, Jsonb(payload.model_dump()), created_by, trace_id),
                ).fetchone()
        except psycopg.Error as exc:
            raise _map(exc) from None
        assert row is not None  # noqa: S101
        return _draft(row)

    def get(self, draft_id: str) -> Draft | None:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    f"SELECT {_COLUMNS} FROM ops.drafts WHERE draft_id = %s::uuid",
                    (draft_id,),
                ).fetchone()
        except psycopg.errors.InvalidTextRepresentation:
            return None
        except psycopg.Error as exc:
            raise _map(exc) from None
        return _draft(row) if row else None

    def list_for(self, created_by: str, status: DraftStatus | None = None) -> list[Draft]:
        query = f"SELECT {_COLUMNS} FROM ops.drafts WHERE created_by = %s"
        args: list[Any] = [created_by]
        if status is not None:
            query += " AND status = %s"
            args.append(status.value)
        try:
            with self._connect() as conn:
                rows = conn.execute(query + " ORDER BY created_at DESC LIMIT 100", args).fetchall()
        except psycopg.Error as exc:
            raise _map(exc) from None
        return [_draft(r) for r in rows]

    def submit(self, draft_id: str, by: str) -> Draft:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "UPDATE ops.drafts SET status = 'submitted', submitted_by = %s, "
                    "submitted_at = now() WHERE draft_id = %s::uuid AND created_by = %s "
                    f"AND status = 'draft' RETURNING {_COLUMNS}",
                    (by, draft_id, by),
                ).fetchone()
        except psycopg.Error as exc:
            raise _map(exc) from None
        if row is None:
            raise DraftError("NOT_FOUND")  # missing, not yours, or not in the draft state
        return _draft(row)


class ApprovalRepo:
    """Used only by the approval service: read the queue and record decisions."""

    def __init__(self, dsn: str, *, connect_timeout: int = 5) -> None:
        self._dsn = dsn
        self._timeout = connect_timeout

    def _connect(self) -> psycopg.Connection[dict[str, Any]]:
        return psycopg.connect(self._dsn, connect_timeout=self._timeout, row_factory=dict_row)

    def queue(self, entities: list[str]) -> list[Draft]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    f"SELECT {_COLUMNS} FROM ops.drafts WHERE status = 'submitted' "
                    "AND entity_id = ANY(%s) ORDER BY submitted_at",
                    (entities,),
                ).fetchall()
        except psycopg.Error as exc:
            raise _map(exc) from None
        return [_draft(r) for r in rows]

    def get(self, draft_id: str) -> Draft | None:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    f"SELECT {_COLUMNS} FROM ops.drafts WHERE draft_id = %s::uuid",
                    (draft_id,),
                ).fetchone()
        except psycopg.errors.InvalidTextRepresentation:
            return None
        except psycopg.Error as exc:
            raise _map(exc) from None
        return _draft(row) if row else None

    def decide(
        self, draft_id: str, decision: Literal["approved", "rejected"], by: str, comment: str | None
    ) -> Draft:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "UPDATE ops.drafts SET status = %s, decided_by = %s, decided_at = now(), "
                    f"decision_comment = %s WHERE draft_id = %s::uuid RETURNING {_COLUMNS}",
                    (decision, by, comment, draft_id),
                ).fetchone()
        except psycopg.Error as exc:
            raise _map(exc) from None
        if row is None:
            raise DraftError("NOT_FOUND")
        return _draft(row)


def can_access_entity(dsn: str, subject: str, entity_id: str) -> bool:
    """Whether the underlying system (database role membership) lets this person see an entity.

    This is the source of truth for entitlements (D6): the service never trusts a claim for it.
    """
    role = user_role(subject)
    if role is None:
        return False
    try:
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            row = conn.execute(
                "SELECT pg_has_role(%s, %s, 'MEMBER')", (role, entity_role(entity_id))
            ).fetchone()
    except psycopg.Error:
        return False  # an unknown user or entity has no access
    return bool(row and row[0])


class PostgresEntitlements:
    """Entitlements read from the database: the same source of truth that scopes data (D6)."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def can_access(self, subject: str, entity_id: str) -> bool:
        return can_access_entity(self._dsn, subject, entity_id)

    def entities_for(self, subject: str) -> list[str]:
        role = user_role(subject)
        if role is None:
            return []
        try:
            with psycopg.connect(self._dsn, connect_timeout=5) as conn:
                rows = conn.execute(
                    "SELECT r.rolname FROM pg_auth_members m "
                    "JOIN pg_roles r ON r.oid = m.roleid JOIN pg_roles u ON u.oid = m.member "
                    "WHERE u.rolname = %s AND r.rolname LIKE 'ent\\_%%'",
                    (role,),
                ).fetchall()
        except psycopg.Error:
            return []
        # ent_hal_de -> HAL-DE (the inverse of query_api.provision.entity_role)
        return sorted(str(r[0])[4:].upper().replace("_", "-") for r in rows)
