"""Drafts MCP server: write to drafts only. There is no tool that approves, rejects or executes.

Creating a draft needs an analyst or manager who is entitled to the draft's entity; the draft is
attributed to that person. Submitting is the creator's own act. Deciding belongs to the separate
approval service, which this server cannot reach (and whose database role it does not hold).
"""

from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal, Protocol, TypeVar

import anyio
from copilot_auth import TokenValidator
from copilot_drafts import Draft, DraftError, DraftPayload, DraftStatus, EvidenceRef
from copilot_mcp import new_server, traced
from copilot_schemas import Evidence, Provenance
from copilot_tracing import Tracing
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, ValidationError

T = TypeVar("T")
_ALLOWED_ROLES = {"analyst", "treasury_manager"}


class DraftsStore(Protocol):
    def create(
        self,
        *,
        kind: Literal["case_file"],
        entity_id: str,
        title: str,
        payload: DraftPayload,
        created_by: str,
        trace_id: str | None,
    ) -> Draft: ...

    def get(self, draft_id: str) -> Draft | None: ...

    def list_for(self, created_by: str, status: DraftStatus | None = None) -> list[Draft]: ...

    def submit(self, draft_id: str, by: str) -> Draft: ...


class Entitlements(Protocol):
    def can_access(self, subject: str, entity_id: str) -> bool: ...


class Deps(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    validator: TokenValidator
    store: Any  # DraftsStore
    entitlements: Any  # Entitlements
    issuer_url: str
    resource_url: str
    tracing: Tracing | None = None


class EvidenceIn(BaseModel):
    source_tool: str
    source_ref: str


class DraftOut(BaseModel):
    draft_id: str
    kind: str
    entity_id: str
    title: str
    summary: str
    recommended_actions: list[str]
    status: str
    created_by: str
    created_at: datetime
    submitted_at: datetime | None
    evidence: list[Evidence]


class DraftList(BaseModel):
    drafts: list[DraftOut]


def _out(draft: Draft, tool: str) -> DraftOut:
    return DraftOut(
        draft_id=draft.draft_id,
        kind=draft.kind,
        entity_id=draft.entity_id,
        title=draft.title,
        summary=draft.payload.summary,
        recommended_actions=draft.payload.recommended_actions,
        status=draft.status.value,
        created_by=draft.created_by,
        created_at=draft.created_at,
        submitted_at=draft.submitted_at,
        evidence=[
            Evidence(
                evidence_id=f"ev-{draft.draft_id[:8]}",
                provenance=Provenance.SYSTEM_STATE,
                source_tool=f"drafts.{tool}",
                source_ref=draft.draft_id,
            )
        ],
    )


def _contains_untrusted(*texts: str) -> bool:
    return any("<untrusted" in t.lower() for t in texts)


def build_server(deps: Deps) -> MCPServer:
    server = new_server(
        "drafts", deps.validator, issuer_url=deps.issuer_url, resource_url=deps.resource_url
    )

    def caller() -> tuple[str, set[str]]:
        token = get_access_token()
        if token is None or not token.subject:
            raise ToolError("UNAUTHENTICATED")
        return token.subject, set(token.scopes)

    def require_maker_role(roles: set[str]) -> None:
        if not roles & _ALLOWED_ROLES:
            raise ToolError("ROLE_REQUIRED")

    async def blocking(call: Callable[[], T]) -> T:
        """Run a synchronous repository call off the event loop, mapping its failures to codes."""
        try:
            return await anyio.to_thread.run_sync(call)
        except DraftError as exc:
            raise ToolError(exc.code) from None

    @server.tool()
    async def create_draft(
        title: str,
        summary: str,
        entity_id: str,
        ctx: Context,
        recommended_actions: list[str] | None = None,
        evidence: list[EvidenceIn] | None = None,
    ) -> DraftOut:
        """Create a case-file draft for a human to review. Nothing is acted on."""
        with traced(deps.tracing, ctx, "drafts", "create_draft"):
            subject, roles = caller()
            require_maker_role(roles)
            actions = recommended_actions or []
            if _contains_untrusted(title, summary, *actions):
                raise ToolError("UNTRUSTED_CONTENT")  # data from outside never enters a draft
            if not await blocking(lambda: deps.entitlements.can_access(subject, entity_id)):
                raise ToolError("NOT_ENTITLED")
            try:
                payload = DraftPayload(
                    summary=summary,
                    recommended_actions=actions,
                    evidence=[EvidenceRef(**e.model_dump()) for e in evidence or []],
                )
            except ValidationError:
                raise ToolError("BAD_REQUEST") from None
            current = deps.tracing.current() if deps.tracing else None
            draft = await blocking(
                lambda: deps.store.create(
                    kind="case_file",
                    entity_id=entity_id,
                    title=title,
                    payload=payload,
                    created_by=subject,
                    trace_id=current.trace_id if current else None,
                )
            )
            return _out(draft, "create_draft")

    @server.tool()
    async def submit_for_review(draft_id: str, ctx: Context) -> DraftOut:
        """Submit your own draft to the approval queue. A person who did not make it decides."""
        with traced(deps.tracing, ctx, "drafts", "submit_for_review"):
            subject, roles = caller()
            require_maker_role(roles)
            draft = await blocking(lambda: deps.store.submit(draft_id, subject))
            return _out(draft, "submit_for_review")

    @server.tool()
    async def list_drafts(ctx: Context, status: str | None = None) -> DraftList:
        """List your own drafts, optionally by status."""
        with traced(deps.tracing, ctx, "drafts", "list_drafts"):
            subject, _ = caller()
            try:
                wanted = DraftStatus(status) if status else None
            except ValueError:
                raise ToolError("BAD_REQUEST") from None
            drafts = await blocking(lambda: deps.store.list_for(subject, wanted))
            return DraftList(drafts=[_out(d, "list_drafts") for d in drafts])

    @server.tool()
    async def get_draft(draft_id: str, ctx: Context) -> DraftOut:
        """Read one of your own drafts."""
        with traced(deps.tracing, ctx, "drafts", "get_draft"):
            subject, _ = caller()
            draft = await blocking(lambda: deps.store.get(draft_id))
            if draft is None or draft.created_by != subject:
                raise ToolError("NOT_FOUND")  # someone else's draft is indistinguishable from none
            return _out(draft, "get_draft")

    return server
