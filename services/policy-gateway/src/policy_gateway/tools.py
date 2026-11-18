"""The tool registry. Every tool has a tier, and no tier can mutate or execute (principle 7)."""

from enum import StrEnum


class Tier(StrEnum):
    READ = "READ"
    ANALYZE = "ANALYZE"
    DRAFT = "DRAFT"  # the highest tier: create a draft record, never act on it


# Adding a name here is a reviewed change. There is deliberately no approve, release, execute
# or mutate tool: approval is a separate human-operated service (PL3).
TOOL_TIERS: dict[str, Tier] = {
    "analytics.list_stores": Tier.READ,
    "analytics.describe_store": Tier.READ,
    "analytics.resolve_business_term": Tier.READ,
    "analytics.run_validated_select": Tier.READ,
    "analytics.get_freshness": Tier.READ,
    "drafts.list_drafts": Tier.READ,
    "drafts.get_draft": Tier.READ,
    "drafts.create_draft": Tier.DRAFT,
    "drafts.submit_for_review": Tier.DRAFT,
}


def server_of(tool: str) -> str:
    return tool.split(".", 1)[0]
