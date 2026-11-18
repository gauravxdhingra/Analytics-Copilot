"""The semantic SQL verifier hook used by `run_validated_select`."""

from dataclasses import dataclass
from typing import Any, Protocol

from copilot_sql_verifier import VerificationFailed as CatalogueRejected
from copilot_sql_verifier import Verifier, VerifyOptions


class VerificationFailed(Exception):  # carries a mapped code and reasons
    """The query was refused. `text` lists each reason so the planner can correct and retry."""

    def __init__(self, code: str, text: str = "", codes: list[str] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.text = text or code
        self.codes = codes or []


@dataclass(frozen=True)
class VerifyFlags:
    include_history: bool = False
    include_cancelled: bool = False
    date_field: str | None = None


class SqlVerifier(Protocol):
    def verify(self, sql: str, params: list[Any], flags: VerifyFlags) -> list[str]:
        """Return notes on what was not checked, or raise VerificationFailed."""


class CatalogueVerifier:
    """Verifies against the semantic catalogue: the finance rules as executable gates."""

    def __init__(self, verifier: Verifier) -> None:
        self._verifier = verifier

    def verify(self, sql: str, params: list[Any], flags: VerifyFlags) -> list[str]:
        options = VerifyOptions(
            history_requested=flags.include_history,
            include_cancelled=flags.include_cancelled,
            date_field=flags.date_field,
        )
        try:
            return self._verifier.verify(sql, params, options)
        except CatalogueRejected as exc:
            reasons = "; ".join(f"{v.code}: {v.message}" for v in exc.violations)
            codes = [v.code for v in exc.violations]
            text = f"VERIFIER_REJECTED [{reasons}]"
            raise VerificationFailed("VERIFIER_REJECTED", text, codes) from None


class PassThroughVerifier:
    """Checks nothing and says so. Kept for tests and for deployments without a catalogue."""

    def verify(self, sql: str, params: list[Any], flags: VerifyFlags) -> list[str]:
        return [
            "unverified: no semantic verifier is installed; only the Query API guard and "
            "row-level security were applied"
        ]
