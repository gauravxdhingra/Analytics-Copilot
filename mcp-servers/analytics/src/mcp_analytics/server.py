"""Analytics MCP server: read-only tools that reach data only through the Query API.

Each tool validates the caller's audience-bound token (done by the token verifier), exchanges it
for a Query API token (never passing the caller's token through) and returns typed output with
Evidence. Errors are mapped codes; raw upstream text never leaves this module.
"""

import hashlib
import json
from collections.abc import Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager, nullcontext
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from copilot_auth import AuthError, TokenValidator
from copilot_catalogue import Catalogue
from copilot_mcp import new_server
from copilot_memory import Owner, ResultCache, cache_key
from copilot_schemas import Evidence, Provenance
from copilot_tracing import Tracing
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from opentelemetry.trace import Span
from pydantic import BaseModel

from mcp_analytics.query_client import QueryApi, UpstreamFailure
from mcp_analytics.verifier import (
    PassThroughVerifier,
    SqlVerifier,
    VerificationFailed,
    VerifyFlags,
)

QUERY_API_AUDIENCE = "query-api"


class TokenExchanger(Protocol):
    async def exchange(self, subject_token: str, audience: str) -> str: ...


@dataclass
class Deps:
    validator: TokenValidator
    catalogue: Catalogue
    query_api: QueryApi
    exchanger: TokenExchanger
    issuer_url: str
    resource_url: str
    verifier: SqlVerifier = field(default_factory=PassThroughVerifier)
    tracing: Tracing | None = None
    cache: ResultCache | None = None  # per user; None means every question runs fresh


class Column(BaseModel):
    name: str
    type: str


class StoreSummary(BaseModel):
    store_id: str
    definition: str
    grain: list[str]


class StoreList(BaseModel):
    stores: list[StoreSummary]
    evidence: list[Evidence]


class CatalogueColumn(BaseModel):
    name: str
    type: str
    description: str
    sensitivity: str
    untrusted: bool


class StoreDescription(BaseModel):
    store_id: str
    definition: str
    grain: list[str]
    columns: list[CatalogueColumn]
    required_filters: list[str]
    dates: dict[str, str]
    money: list[str]
    relationships: list[str]
    known_issues: list[str]
    catalogue_version: str
    evidence: list[Evidence]


class TermDefinition(BaseModel):
    term: str
    definition: str
    maps_to: str
    evidence: list[Evidence]


class Freshness(BaseModel):
    as_of: str | None
    evidence: list[Evidence]


class SelectResult(BaseModel):
    columns: list[Column]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    as_of: str | None
    query_hash: str
    untrusted_columns: list[str]
    warnings: list[str]
    catalogue_version: str
    evidence: list[Evidence]
    cached: bool = False  # True when the rows came from this user's own recent identical query


def _evidence(tool: str, ref: str, as_of: str | None) -> Evidence:
    return Evidence(
        evidence_id="ev-" + hashlib.sha256(f"{tool}:{ref}".encode()).hexdigest()[:12],
        provenance=Provenance.SYSTEM_STATE,
        source_tool=f"analytics.{tool}",
        source_ref=ref,
        snapshot_at=datetime.fromisoformat(as_of) if as_of else None,
    )


def _query_hash(sql: str, params: list[Any]) -> str:
    canonical = json.dumps({"sql": sql, "params": params}, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def build_server(deps: Deps) -> MCPServer:
    server = new_server(
        "analytics", deps.validator, issuer_url=deps.issuer_url, resource_url=deps.resource_url
    )

    @contextmanager
    def traced(ctx: Context, tool: str) -> Iterator[None]:
        """Continue the caller's trace (carried in the MCP request meta) around a tool call."""
        if deps.tracing is None:
            yield
            return
        carrier = {k: str(v) for k, v in dict(ctx.request_context.meta or {}).items()}
        with ExitStack() as stack:
            stack.enter_context(deps.tracing.attach(carrier))
            stack.enter_context(deps.tracing.span("mcp.tool", tool=f"analytics.{tool}"))
            yield

    async def query_api_token() -> str:
        caller = get_access_token()
        if caller is None:
            raise ToolError("UNAUTHENTICATED")
        try:
            return await deps.exchanger.exchange(caller.token, QUERY_API_AUDIENCE)
        except AuthError:
            raise ToolError("UNAUTHENTICATED") from None

    def _cache_owner() -> Owner | None:
        """Whose cache this is: the validated caller's tenant and user, never anything sent in."""
        caller = get_access_token()
        tenant = (caller.claims or {}).get("tenant_id") if caller else None
        if caller is None or not caller.subject or not isinstance(tenant, str):
            return None  # without a clear owner there is no cache
        return Owner(tenant_id=tenant, user_id=caller.subject)

    async def guarded(call: Any) -> Any:
        try:
            return await call
        except UpstreamFailure as exc:
            raise ToolError(exc.code) from None
        except VerificationFailed as exc:
            raise ToolError(exc.code) from None

    @server.tool()
    async def list_stores(ctx: Context) -> StoreList:
        """List the stores the caller can query, with what each one means and its grain."""
        with traced(ctx, "list_stores"):
            schema = await guarded(deps.query_api.schema(await query_api_token()))
        visible = {f"{t['schema']}.{t['table']}" for t in schema["tables"]}
        stores = [
            StoreSummary(store_id=name, definition=store.definition, grain=store.grain)
            for name, store in sorted(deps.catalogue.stores.items())
            if name in visible
        ]
        return StoreList(stores=stores, evidence=[_evidence("list_stores", "catalogue", None)])

    @server.tool()
    async def describe_store(store_id: str, ctx: Context) -> StoreDescription:
        """Describe one store: meaning, grain, mandatory filters, money, joins and known issues."""
        with traced(ctx, "describe_store"):
            store = deps.catalogue.stores.get(store_id)
        if store is None:
            raise ToolError("STORE_NOT_FOUND")
        return StoreDescription(
            store_id=store_id,
            definition=store.definition,
            grain=store.grain,
            columns=[
                CatalogueColumn(
                    name=c.name,
                    type=c.type,
                    description=c.description,
                    sensitivity=c.sensitivity,
                    untrusted=c.untrusted,
                )
                for c in store.columns
            ],
            required_filters=[
                f"{f.column} = {f.value}" + (f" (waived by: {f.waived_by})" if f.waived_by else "")
                for f in store.required_filters
            ],
            dates=store.dates,
            money=[f"{m.column} in {m.currency_column}" for m in store.money],
            relationships=[f"{r.name} ({r.cardinality})" for r in store.relationships],
            known_issues=store.known_issues,
            catalogue_version=deps.catalogue.version,
            evidence=[_evidence("describe_store", store_id, None)],
        )

    @server.tool()
    async def resolve_business_term(term: str, ctx: Context) -> TermDefinition:
        """Look up a business term in the glossary and how it maps onto the stores."""
        with traced(ctx, "resolve_business_term"):
            found = deps.catalogue.glossary.lookup(term)
        if found is None:
            raise ToolError("TERM_NOT_FOUND")
        return TermDefinition(
            term=term.strip().lower(),
            definition=found.definition,
            maps_to=found.maps_to,
            evidence=[_evidence("resolve_business_term", term.strip().lower(), None)],
        )

    @server.tool()
    async def get_freshness(ctx: Context) -> Freshness:
        """When the data was last loaded. Every answer should say as of when."""
        with traced(ctx, "get_freshness"):
            reply = await guarded(deps.query_api.freshness(await query_api_token()))
        return Freshness(
            as_of=reply.get("as_of"),
            evidence=[_evidence("get_freshness", "freshness", reply.get("as_of"))],
        )

    def _verify(sql: str, bound: list[Any], flags: VerifyFlags) -> list[str]:
        """Run the verifier inside a `verifier.check` span: pass or fail, with reason codes."""
        span_cm: AbstractContextManager[Span | None] = (
            deps.tracing.span("verifier.check", check="catalogue")
            if deps.tracing
            else nullcontext(None)
        )
        with span_cm as span:
            try:
                warnings = deps.verifier.verify(sql, bound, flags)
            except VerificationFailed as exc:
                if span is not None:
                    span.set_attribute("passed", False)
                    span.set_attribute("codes", exc.codes)
                raise ToolError(exc.text) from None
            if span is not None:
                span.set_attribute("passed", True)
            return warnings

    @server.tool()
    async def run_validated_select(
        sql: str,
        purpose: str,
        params: list[str | int | bool | None] | None = None,
        expected_grain: str | None = None,
        max_rows: int = 1000,
        include_history: bool = False,
        include_cancelled: bool = False,
        date_field: str | None = None,
        *,
        ctx: Context,
    ) -> SelectResult:
        """Run one verified, parameterised SELECT as the caller and return capped rows."""
        bound: list[Any] = list(params or [])
        flags = VerifyFlags(include_history, include_cancelled, date_field)
        with traced(ctx, "run_validated_select"):
            # Authentication happened at the transport and verification happens here, both
            # before the cache is consulted: a hit only saves the Query API call.
            warnings = _verify(sql, bound, flags)
            owner = _cache_owner()
            key = cache_key(
                sql,
                bound,
                {
                    "max_rows": max_rows,
                    "history": include_history,
                    "cancelled": include_cancelled,
                    "date_field": date_field,
                },
            )
            hit = deps.cache.get(owner, key) if deps.cache and owner else None
            if hit is not None:
                reply = hit
            else:
                reply = await guarded(
                    deps.query_api.query(
                        await query_api_token(),
                        sql=sql,
                        params=bound,
                        purpose=purpose,
                        max_rows=max_rows,
                        timeout_ms=30_000,
                    )
                )
                if deps.cache and owner:  # only a successful reply gets here: errors are never kept
                    deps.cache.put(owner, key, reply)
        query_hash = _query_hash(sql, bound)
        names = [c["name"] for c in reply["columns"]]
        return SelectResult(
            columns=[Column(**c) for c in reply["columns"]],
            rows=reply["rows"],
            row_count=reply["row_count"],
            truncated=reply["truncated"],
            as_of=reply.get("as_of"),
            query_hash=query_hash,
            untrusted_columns=[c for c in names if c in deps.catalogue.untrusted_columns()],
            warnings=warnings,
            catalogue_version=deps.catalogue.version,
            evidence=[_evidence("run_validated_select", query_hash, reply.get("as_of"))],
            cached=hit is not None,
        )

    return server
