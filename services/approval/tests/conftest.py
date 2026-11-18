from collections.abc import Callable
from typing import Any

import pytest
from approval.app import ApprovalConfig, create_app
from approval.testing import APPROVER, AUDIENCE, MAKER, NOW, FakeEntitlements, FakeRepo
from copilot_auth import StaticKeyProvider, TokenValidator
from copilot_auth.testing import ISSUER, KID, mint_token
from copilot_drafts import DraftPayload, DraftStatus
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from fastapi import FastAPI
from policy_gateway.ledger import InMemoryLedger


@pytest.fixture(scope="session")
def key() -> RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def repo() -> FakeRepo:
    return FakeRepo()


@pytest.fixture
def entitlements() -> FakeEntitlements:
    return FakeEntitlements({APPROVER: {"HAL-DE", "HAL-UK"}})


@pytest.fixture
def ledger() -> InMemoryLedger:
    return InMemoryLedger()


@pytest.fixture
def app(
    key: RSAPrivateKey, repo: FakeRepo, entitlements: FakeEntitlements, ledger: InMemoryLedger
) -> FastAPI:
    validator = TokenValidator(
        issuer=ISSUER,
        audience=AUDIENCE,
        keys=StaticKeyProvider({KID: key.public_key()}),
        clock=lambda: NOW,
    )
    return create_app(
        ApprovalConfig(),
        validator=validator,
        repo=repo,
        entitlements=entitlements,
        ledger=ledger,
        clock=lambda: NOW,
    )


@pytest.fixture
def token_for(key: RSAPrivateKey) -> Callable[..., str]:
    def make(subject: str = APPROVER, **claims: Any) -> str:
        defaults: dict[str, Any] = {
            "aud": AUDIENCE,
            "sub": subject,
            "roles": ["approver"],
            "acr": "2",
            "auth_time": int(NOW.timestamp()) - 60,
            "azp": "copilot-bff",
        }
        return mint_token(key, **(defaults | claims))

    return make


@pytest.fixture
def submitted(repo: FakeRepo) -> str:
    """A draft created and submitted by MAKER, waiting for a decision."""
    draft = repo.add(
        entity_id="HAL-DE",
        created_by=MAKER,
        submitted_by=MAKER,
        status=DraftStatus.SUBMITTED,
        payload=DraftPayload(summary="Three payments sit just under the limit."),
    )
    return draft.draft_id
