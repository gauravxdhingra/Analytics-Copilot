"""Append-only, hash-chained ledger of every gateway decision (Analytics Copilot.md §5.2)."""

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol

GENESIS = "sha256:" + "0" * 64


class LedgerError(Exception):
    """The ledger could not record a decision. The call is then denied (fail closed)."""


@dataclass(frozen=True)
class LedgerDraft:
    run_id: str
    trace_id: str
    tenant_id: str
    agent_id: str
    user_id: str
    tool: str
    args_hash: str
    args_redacted: dict[str, Any]
    decision: str
    reason_code: str | None
    policy_version: int
    result_hash: str | None


@dataclass(frozen=True)
class LedgerEntry:
    seq: int
    ts: datetime
    run_id: str
    trace_id: str
    tenant_id: str
    agent_id: str
    user_id: str
    tool: str
    args_hash: str
    args_redacted: dict[str, Any]
    decision: str
    reason_code: str | None
    policy_version: int
    result_hash: str | None
    prev_hash: str
    row_hash: str


def compute_row_hash(entry: LedgerEntry) -> str:
    body = {k: v for k, v in asdict(entry).items() if k != "row_hash"}
    canonical = json.dumps(body, sort_keys=True, default=str, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def seal(draft: LedgerDraft, *, seq: int, prev_hash: str, ts: datetime) -> LedgerEntry:
    entry = LedgerEntry(seq=seq, ts=ts, prev_hash=prev_hash, row_hash="", **asdict(draft))
    return replace(entry, row_hash=compute_row_hash(entry))


def chain_is_valid(entries: list[LedgerEntry]) -> bool:
    prev = GENESIS
    for expected_seq, entry in enumerate(entries, start=1):
        if entry.seq != expected_seq or entry.prev_hash != prev:
            return False
        if entry.row_hash != compute_row_hash(entry):
            return False
        prev = entry.row_hash
    return True


class Ledger(Protocol):
    def append(self, draft: LedgerDraft) -> LedgerEntry: ...


class InMemoryLedger:
    def __init__(self, *, fail: bool = False) -> None:
        self.entries: list[LedgerEntry] = []
        self._fail = fail

    def append(self, draft: LedgerDraft) -> LedgerEntry:
        if self._fail:
            raise LedgerError("ledger unavailable")
        prev = self.entries[-1].row_hash if self.entries else GENESIS
        entry = seal(draft, seq=len(self.entries) + 1, prev_hash=prev, ts=datetime.now(UTC))
        self.entries.append(entry)
        return entry

    def verify_chain(self) -> bool:
        return chain_is_valid(self.entries)

    def tamper(self, index: int, **changes: Any) -> None:  # test helper
        self.entries[index] = replace(self.entries[index], **changes)
