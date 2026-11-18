"""Database roles and row-level security: the underlying system's own authorization (D6).

Run by the database owner after the data is loaded. The Query API itself only holds the login
role, which has no data grants and can do nothing but `SET ROLE` to a user role. Each user
role is a member of one `ent_*` role per entity the user may see, and row-level security keys on
that membership. Scope is therefore decided by the database, not by anything in a request.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg import sql

LOGIN_ROLE = "copilot_query"
_SCOPED_TABLES = ("entities", "accounts", "transactions", "payments")
_OPEN_TABLES = ("banks", "counterparties", "ssis", "fx_rates")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def entity_role(entity_id: str) -> str:
    return "ent_" + re.sub(r"[^a-z0-9]+", "_", entity_id.lower())


def user_role(subject: str) -> str | None:
    """Database role for a token subject, or None if the subject is not a UUID (fail closed)."""
    if not _UUID.match(subject):
        return None
    return "u_" + subject.replace("-", "")


@dataclass(frozen=True)
class IdentityUser:
    id: str
    username: str
    entities: tuple[str, ...]
    roles: tuple[str, ...]

    @property
    def db_role(self) -> str:
        role = user_role(self.id)
        if role is None:
            raise ValueError(f"user id is not a UUID: {self.username}")
        return role


def load_identity(path: Path) -> list[IdentityUser]:
    data = json.loads(path.read_text())
    return [
        IdentityUser(
            id=u["id"],
            username=u["username"],
            entities=tuple(u["entities"]),
            roles=tuple(u["roles"]),
        )
        for u in data["users"]
    ]


def _ident(name: str) -> sql.Composable:
    return sql.Identifier(name)


def _create_role_if_missing(conn: psycopg.Connection, role: str, options: str = "") -> None:
    exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
    if not exists:
        conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN " + options).format(_ident(role)))


def provision(owner_dsn: str, users: list[IdentityUser], *, login_password: str) -> None:
    with psycopg.connect(owner_dsn, autocommit=False) as conn:
        entities = [r[0] for r in conn.execute("SELECT entity_id FROM treasury.entities")]

        # Lock the schemas down first; grants below add back only what is needed.
        conn.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        for schema in ("treasury", "docs", "eval"):
            conn.execute(sql.SQL("REVOKE ALL ON SCHEMA {} FROM PUBLIC").format(_ident(schema)))
        for function in ("set_config(text, text, boolean)", "pg_sleep(double precision)"):
            conn.execute(
                sql.SQL("REVOKE EXECUTE ON FUNCTION pg_catalog.{} FROM PUBLIC").format(
                    sql.SQL(function)
                )
            )

        # Login role: no data privileges of its own.
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (LOGIN_ROLE,)).fetchone()
        verb = "ALTER" if exists else "CREATE"
        conn.execute(
            sql.SQL(
                verb + " ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT "
                "CONNECTION LIMIT 20 PASSWORD {}"
            ).format(_ident(LOGIN_ROLE), sql.Literal(login_password))
        )

        # One role per entity, holding the table privileges.
        for entity in entities:
            role = entity_role(entity)
            _create_role_if_missing(conn, role)
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA treasury TO {}").format(_ident(role)))
            for table in (*_SCOPED_TABLES, *_OPEN_TABLES, "daily_balances"):
                conn.execute(
                    sql.SQL("GRANT SELECT ON treasury.{} TO {}").format(_ident(table), _ident(role))
                )

        # One role per person; membership in ent_* roles is their scope.
        for user in users:
            role = user.db_role
            _create_role_if_missing(conn, role)
            for entity in entities:
                conn.execute(
                    sql.SQL("REVOKE {} FROM {}").format(_ident(entity_role(entity)), _ident(role))
                )
            for entity in user.entities:
                conn.execute(
                    sql.SQL("GRANT {} TO {}").format(_ident(entity_role(entity)), _ident(role))
                )
            # The login role may switch into the user role but inherits nothing from it.
            conn.execute(
                sql.SQL("GRANT {} TO {} WITH INHERIT FALSE, SET TRUE").format(
                    _ident(role), _ident(LOGIN_ROLE)
                )
            )

        # Row-level security, forced even for table owners.
        for table in (*_SCOPED_TABLES, "daily_balances"):
            conn.execute(
                sql.SQL("ALTER TABLE treasury.{} ENABLE ROW LEVEL SECURITY").format(_ident(table))
            )
            conn.execute(
                sql.SQL("ALTER TABLE treasury.{} FORCE ROW LEVEL SECURITY").format(_ident(table))
            )
            conn.execute(
                sql.SQL("DROP POLICY IF EXISTS entity_scope ON treasury.{}").format(_ident(table))
            )
        member = (
            "pg_has_role(current_user, 'ent_' || regexp_replace(lower(entity_id), "
            "'[^a-z0-9]+', '_', 'g'), 'MEMBER')"
        )
        for table in _SCOPED_TABLES:
            conn.execute(
                sql.SQL(
                    "CREATE POLICY entity_scope ON treasury.{} FOR SELECT USING (" + member + ")"
                ).format(_ident(table))
            )
        # Balances have no entity column: they follow the accounts the user can see.
        conn.execute(
            "CREATE POLICY entity_scope ON treasury.daily_balances FOR SELECT USING ("
            "EXISTS (SELECT 1 FROM treasury.accounts a "
            "WHERE a.account_id = daily_balances.account_id))"
        )
