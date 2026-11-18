"""Drafts: the highest tier of action the copilot has. A human decides."""

from copilot_drafts.models import Draft, DraftPayload, DraftStatus, EvidenceRef
from copilot_drafts.repos import (
    ApprovalRepo,
    DraftError,
    DraftsRepo,
    PostgresEntitlements,
    can_access_entity,
)
from copilot_drafts.schema import APPROVAL_ROLE, DRAFTS_ROLE, ensure_schema

__all__ = [
    "APPROVAL_ROLE",
    "DRAFTS_ROLE",
    "ApprovalRepo",
    "Draft",
    "DraftError",
    "DraftPayload",
    "DraftStatus",
    "DraftsRepo",
    "EvidenceRef",
    "PostgresEntitlements",
    "can_access_entity",
    "ensure_schema",
]
