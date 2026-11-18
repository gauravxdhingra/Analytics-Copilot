"""Every approval rule has its own refusal. A rule without a test is not a control."""

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from approval.testing import APPROVER, MAKER, FakeEntitlements, FakeRepo
from copilot_drafts import DraftStatus
from fastapi import FastAPI
from policy_gateway.ledger import InMemoryLedger

Token = Callable[..., str]


async def _post(app: FastAPI, path: str, token: str | None, body: dict[str, Any]) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://approval"
    ) as client:
        return await client.post(path, json=body, headers=headers)


async def _get(app: FastAPI, path: str, token: str | None) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://approval"
    ) as client:
        return await client.get(path, headers=headers)


def _decide(draft_id: str) -> tuple[str, dict[str, Any]]:
    return f"/v1/drafts/{draft_id}/decision", {"decision": "approve", "comment": "Reviewed."}


async def test_an_entitled_approver_with_strong_authentication_can_approve(
    app: FastAPI, token_for: Token, submitted: str, repo: FakeRepo, ledger: InMemoryLedger
) -> None:
    path, body = _decide(submitted)
    response = await _post(app, path, token_for(), body)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "approved" and response.json()["decided_by"] == APPROVER
    assert repo.get(submitted).status is DraftStatus.APPROVED  # type: ignore[union-attr]
    assert ledger.entries[-1].decision == "ALLOW" and ledger.verify_chain()
    assert ledger.entries[-1].tool == "drafts.decision" and ledger.entries[-1].user_id == APPROVER


async def test_a_rejection_records_the_comment(
    app: FastAPI, token_for: Token, submitted: str, repo: FakeRepo
) -> None:
    path, _ = _decide(submitted)
    response = await _post(app, path, token_for(), {"decision": "reject", "comment": "Not enough."})
    assert response.status_code == 200 and response.json()["status"] == "rejected"
    assert repo.get(submitted).decision_comment == "Not enough."  # type: ignore[union-attr]


# --- who is calling -------------------------------------------------------------------------------


@pytest.mark.parametrize("header", [None, "garbage"])
async def test_missing_or_bad_tokens_are_refused(
    app: FastAPI, submitted: str, header: str | None
) -> None:
    path, body = _decide(submitted)
    response = await _post(app, path, header, body)
    assert response.status_code == 401 and response.json()["error_code"] == "UNAUTHENTICATED"


async def test_a_token_for_another_audience_is_refused(
    app: FastAPI, token_for: Token, submitted: str
) -> None:
    path, body = _decide(submitted)
    response = await _post(app, path, token_for(aud="orchestrator"), body)
    assert response.status_code == 401


async def test_the_copilots_own_service_identity_cannot_approve(
    app: FastAPI, token_for: Token, submitted: str, repo: FakeRepo
) -> None:
    """The orchestrator's token has no approver role, whatever else it carries."""
    path, body = _decide(submitted)
    response = await _post(app, path, token_for("orchestrator", roles=["analyst"]), body)
    assert response.status_code == 403 and response.json()["error_code"] == "ROLE_REQUIRED"
    assert repo.get(submitted).status is DraftStatus.SUBMITTED  # type: ignore[union-attr]


@pytest.mark.parametrize("roles", [["analyst"], ["treasury_manager"], []])
async def test_only_the_approver_role_may_decide(
    app: FastAPI, token_for: Token, submitted: str, roles: list[str]
) -> None:
    path, body = _decide(submitted)
    response = await _post(app, path, token_for(roles=roles), body)
    assert response.status_code == 403 and response.json()["error_code"] == "ROLE_REQUIRED"


# --- maker-checker and entitlements ---------------------------------------------------------------


async def test_the_maker_cannot_approve_their_own_draft_even_with_the_approver_role(
    app: FastAPI, token_for: Token, submitted: str, entitlements: FakeEntitlements, repo: FakeRepo
) -> None:
    entitlements.grant(MAKER, "HAL-DE")
    path, body = _decide(submitted)
    response = await _post(app, path, token_for(MAKER), body)
    assert response.status_code == 403 and response.json()["error_code"] == "SOD_VIOLATION"
    assert repo.get(submitted).status is DraftStatus.SUBMITTED  # type: ignore[union-attr]


async def test_the_submitter_cannot_approve_what_they_submitted(
    app: FastAPI, token_for: Token, repo: FakeRepo, entitlements: FakeEntitlements
) -> None:
    entitlements.grant("submitter-2", "HAL-DE")
    draft = repo.add(
        entity_id="HAL-DE",
        created_by=MAKER,
        submitted_by="submitter-2",
        status=DraftStatus.SUBMITTED,
    )
    path, body = _decide(draft.draft_id)
    response = await _post(app, path, token_for("submitter-2"), body)
    assert response.status_code == 403 and response.json()["error_code"] == "SOD_VIOLATION"


async def test_an_approver_must_be_entitled_to_the_drafts_entity(
    app: FastAPI, token_for: Token, repo: FakeRepo, entitlements: FakeEntitlements
) -> None:
    entitlements.revoke_all("approver-uk")
    entitlements.grant("approver-uk", "HAL-UK")
    draft = repo.add(
        entity_id="HAL-DE", created_by=MAKER, submitted_by=MAKER, status=DraftStatus.SUBMITTED
    )
    path, body = _decide(draft.draft_id)
    response = await _post(app, path, token_for("approver-uk"), body)
    assert response.status_code == 403 and response.json()["error_code"] == "NOT_ENTITLED"


async def test_entitlement_comes_from_the_underlying_system_not_from_a_claim(
    app: FastAPI, token_for: Token, submitted: str
) -> None:
    path, body = _decide(submitted)
    forged = token_for("nobody", entities=["HAL-DE", "HAL-UK"])  # the claim says yes
    response = await _post(app, path, forged, body)
    assert response.status_code == 403 and response.json()["error_code"] == "NOT_ENTITLED"


# --- step-up authentication -----------------------------------------------------------------------


@pytest.mark.parametrize("claims", [{"acr": None}, {"acr": "1"}, {"acr": "pw"}])
async def test_a_password_only_session_is_not_strong_enough(
    app: FastAPI, token_for: Token, submitted: str, claims: dict[str, Any]
) -> None:
    path, body = _decide(submitted)
    response = await _post(app, path, token_for(**claims), body)
    assert response.status_code == 403 and response.json()["error_code"] == "STEP_UP_REQUIRED"


async def test_a_strong_but_stale_authentication_must_be_repeated(
    app: FastAPI, token_for: Token, submitted: str
) -> None:
    path, body = _decide(submitted)
    stale = token_for(auth_time=1_700_000_000)
    response = await _post(app, path, stale, body)
    assert response.status_code == 403 and response.json()["error_code"] == "STEP_UP_REQUIRED"
    missing = await _post(app, path, token_for(auth_time=None), body)
    assert missing.status_code == 403


# --- state of the draft --------------------------------------------------------------------------------


async def test_only_submitted_drafts_can_be_decided(
    app: FastAPI, token_for: Token, repo: FakeRepo, entitlements: FakeEntitlements
) -> None:
    unsubmitted = repo.add(entity_id="HAL-DE", created_by=MAKER)
    path, body = _decide(unsubmitted.draft_id)
    response = await _post(app, path, token_for(), body)
    assert response.status_code == 409 and response.json()["error_code"] == "NOT_SUBMITTED"


async def test_a_decision_cannot_be_made_twice(
    app: FastAPI, token_for: Token, submitted: str
) -> None:
    path, body = _decide(submitted)
    assert (await _post(app, path, token_for(), body)).status_code == 200
    again = await _post(app, path, token_for(), {"decision": "reject"})
    assert again.status_code == 409 and again.json()["error_code"] == "NOT_SUBMITTED"


async def test_an_unknown_draft_is_not_found(app: FastAPI, token_for: Token) -> None:
    response = await _post(app, "/v1/drafts/no-such/decision", token_for(), {"decision": "approve"})
    assert response.status_code == 404 and response.json()["error_code"] == "NOT_FOUND"


async def test_the_decision_must_be_approve_or_reject(
    app: FastAPI, token_for: Token, submitted: str
) -> None:
    path, _ = _decide(submitted)
    response = await _post(app, path, token_for(), {"decision": "execute"})
    assert response.status_code == 422 and response.json()["error_code"] == "BAD_REQUEST"


# --- fail closed and auditing ----------------------------------------------------------------------------


async def test_a_failing_ledger_blocks_the_decision(
    key: Any, repo: FakeRepo, entitlements: FakeEntitlements, token_for: Token, submitted: str
) -> None:
    from approval.app import ApprovalConfig, create_app
    from approval.testing import AUDIENCE, NOW
    from copilot_auth import StaticKeyProvider, TokenValidator
    from copilot_auth.testing import ISSUER, KID

    validator = TokenValidator(
        issuer=ISSUER,
        audience=AUDIENCE,
        keys=StaticKeyProvider({KID: key.public_key()}),
        clock=lambda: NOW,
    )
    app = create_app(
        ApprovalConfig(),
        validator=validator,
        repo=repo,
        entitlements=entitlements,
        ledger=InMemoryLedger(fail=True),
        clock=lambda: NOW,
    )
    path, body = _decide(submitted)
    response = await _post(app, path, token_for(), body)
    assert response.status_code == 503 and response.json()["error_code"] == "UNAVAILABLE"
    assert repo.get(submitted).status is DraftStatus.SUBMITTED  # type: ignore[union-attr]


async def test_refusals_are_ledgered_too_with_their_reason(
    app: FastAPI, token_for: Token, submitted: str, ledger: InMemoryLedger
) -> None:
    path, body = _decide(submitted)
    await _post(app, path, token_for(roles=["analyst"]), body)
    entry = ledger.entries[-1]
    assert entry.decision == "DENY" and entry.reason_code == "ROLE_REQUIRED"
    assert ledger.verify_chain()


# --- the queue ------------------------------------------------------------------------------------------------


async def test_the_queue_lists_only_submitted_drafts_in_the_approvers_entities(
    app: FastAPI, token_for: Token, repo: FakeRepo, entitlements: FakeEntitlements
) -> None:
    entitlements.revoke_all("approver-de")
    entitlements.grant("approver-de", "HAL-DE")
    repo.add(entity_id="HAL-DE", created_by=MAKER, submitted_by=MAKER, status=DraftStatus.SUBMITTED)
    repo.add(entity_id="HAL-UK", created_by=MAKER, submitted_by=MAKER, status=DraftStatus.SUBMITTED)
    repo.add(entity_id="HAL-DE", created_by=MAKER)  # not submitted
    response = await _get(app, "/v1/queue", token_for("approver-de"))
    assert response.status_code == 200
    items = response.json()["drafts"]
    assert [d["entity_id"] for d in items] == ["HAL-DE"]


async def test_the_queue_needs_the_approver_role(app: FastAPI, token_for: Token) -> None:
    response = await _get(app, "/v1/queue", token_for(roles=["analyst"]))
    assert response.status_code == 403


async def test_health_needs_no_token_and_reveals_nothing(app: FastAPI) -> None:
    response = await _get(app, "/healthz", None)
    assert response.status_code == 200 and response.json() == {"status": "ok"}
