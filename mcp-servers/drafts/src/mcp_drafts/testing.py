"""Test doubles for the drafts server."""

from datetime import UTC, datetime
from typing import Literal

from copilot_drafts import Draft, DraftError, DraftPayload, DraftStatus


class FakeStore:
    def __init__(self) -> None:
        self.drafts: dict[str, Draft] = {}

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
        draft = Draft(
            draft_id=f"00000000-0000-0000-0000-{len(self.drafts) + 1:012d}",
            kind=kind,
            entity_id=entity_id,
            title=title,
            payload=payload,
            status=DraftStatus.DRAFT,
            created_by=created_by,
            created_at=datetime(2026, 11, 18, tzinfo=UTC),
            trace_id=trace_id,
        )
        self.drafts[draft.draft_id] = draft
        return draft

    def get(self, draft_id: str) -> Draft | None:
        return self.drafts.get(draft_id)

    def list_for(self, created_by: str, status: DraftStatus | None = None) -> list[Draft]:
        return [
            d
            for d in self.drafts.values()
            if d.created_by == created_by and (status is None or d.status is status)
        ]

    def submit(self, draft_id: str, by: str) -> Draft:
        draft = self.drafts.get(draft_id)
        if draft is None or draft.created_by != by or draft.status is not DraftStatus.DRAFT:
            raise DraftError("NOT_FOUND")
        updated = draft.model_copy(
            update={
                "status": DraftStatus.SUBMITTED,
                "submitted_by": by,
                "submitted_at": datetime(2026, 11, 18, tzinfo=UTC),
            }
        )
        self.drafts[draft_id] = updated
        return updated


class FakeEntitlements:
    def __init__(self, grants: dict[str, set[str]]) -> None:
        self._grants = grants

    def can_access(self, subject: str, entity_id: str) -> bool:
        return entity_id in self._grants.get(subject, set())
