"""Test doubles for the approval service: an in-memory repo and entitlements."""

from datetime import UTC, datetime

from copilot_drafts import Draft, DraftError, DraftPayload, DraftStatus

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
AUDIENCE = "approval-service"
APPROVER = "approver-1"
MAKER = "maker-1"


class FakeRepo:
    def __init__(self) -> None:
        self._drafts: dict[str, Draft] = {}

    def add(
        self,
        *,
        entity_id: str,
        created_by: str,
        submitted_by: str | None = None,
        status: DraftStatus = DraftStatus.DRAFT,
        payload: DraftPayload | None = None,
    ) -> Draft:
        draft = Draft(
            draft_id=f"d{len(self._drafts) + 1}",
            kind="case_file",
            entity_id=entity_id,
            title="Review near-limit payments",
            payload=payload or DraftPayload(summary="A summary."),
            status=status,
            created_by=created_by,
            created_at=datetime(2026, 11, 18, tzinfo=UTC),
            submitted_by=submitted_by,
        )
        self._drafts[draft.draft_id] = draft
        return draft

    def get(self, draft_id: str) -> Draft | None:
        return self._drafts.get(draft_id)

    def queue(self, entities: list[str]) -> list[Draft]:
        return [
            d
            for d in self._drafts.values()
            if d.status is DraftStatus.SUBMITTED and d.entity_id in entities
        ]

    def decide(self, draft_id: str, decision: str, by: str, comment: str | None) -> Draft:
        draft = self._drafts.get(draft_id)
        if draft is None:
            raise DraftError("NOT_FOUND")
        if draft.status is not DraftStatus.SUBMITTED:
            raise DraftError("ILLEGAL_TRANSITION")
        if by in (draft.created_by, draft.submitted_by):
            raise DraftError("SOD_VIOLATION")
        updated = draft.model_copy(
            update={
                "status": DraftStatus(decision),
                "decided_by": by,
                "decision_comment": comment,
                "decided_at": datetime(2026, 11, 18, tzinfo=UTC),
            }
        )
        self._drafts[draft_id] = updated
        return updated


class FakeEntitlements:
    def __init__(self, grants: dict[str, set[str]] | None = None) -> None:
        self._grants = {k: set(v) for k, v in (grants or {}).items()}

    def grant(self, subject: str, entity_id: str) -> None:
        self._grants.setdefault(subject, set()).add(entity_id)

    def revoke_all(self, subject: str) -> None:
        self._grants.pop(subject, None)

    def can_access(self, subject: str, entity_id: str) -> bool:
        return entity_id in self._grants.get(subject, set())

    def entities_for(self, subject: str) -> list[str]:
        return sorted(self._grants.get(subject, set()))
