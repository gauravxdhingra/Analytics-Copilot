import base64
import hashlib
import hmac
import json
from collections.abc import Callable
from datetime import timedelta

import jwt
import pytest
from copilot_auth import AuthError, AuthErrorCode, StaticKeyProvider, TokenValidator
from copilot_auth.testing import AUDIENCE, ISSUER, KID, NOW
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

MakeToken = Callable[..., str]


@pytest.fixture
def validator(private_key: RSAPrivateKey) -> TokenValidator:
    return TokenValidator(
        issuer=ISSUER,
        audience=AUDIENCE,
        keys=StaticKeyProvider({KID: private_key.public_key()}),
        clock=lambda: NOW,
    )


def _code(validator: TokenValidator, token: str) -> AuthErrorCode:
    with pytest.raises(AuthError) as info:
        validator.validate(token)
    return info.value.code


def test_valid_token_yields_principal(validator: TokenValidator, make_token: MakeToken) -> None:
    principal = validator.validate(make_token())
    assert principal.subject == "user-1"
    assert principal.tenant_id == "halcyon"
    assert principal.entities == ("HAL-DE",)
    assert principal.roles == ("analyst",)
    assert principal.authorized_party == "copilot-bff"


def test_audience_may_be_a_list_containing_ours(
    validator: TokenValidator, make_token: MakeToken
) -> None:
    assert validator.validate(make_token(aud=["copilot-bff", AUDIENCE])).subject == "user-1"


def test_wrong_audience_is_rejected(validator: TokenValidator, make_token: MakeToken) -> None:
    assert _code(validator, make_token(aud="mcp-analytics")) is AuthErrorCode.WRONG_AUDIENCE
    assert _code(validator, make_token(aud=["copilot-bff"])) is AuthErrorCode.WRONG_AUDIENCE


def test_wrong_issuer_is_rejected(validator: TokenValidator, make_token: MakeToken) -> None:
    assert _code(validator, make_token(iss="http://evil/realms/x")) is AuthErrorCode.WRONG_ISSUER


def test_expired_token_is_rejected_using_the_injected_clock(
    validator: TokenValidator, make_token: MakeToken
) -> None:
    expired = make_token(exp=int((NOW - timedelta(seconds=1)).timestamp()))
    assert _code(validator, expired) is AuthErrorCode.TOKEN_EXPIRED
    at_expiry = make_token(exp=int(NOW.timestamp()))
    assert _code(validator, at_expiry) is AuthErrorCode.TOKEN_EXPIRED  # exp is exclusive


def test_missing_exp_is_rejected(validator: TokenValidator, make_token: MakeToken) -> None:
    assert _code(validator, make_token(exp=None)) is AuthErrorCode.TOKEN_INVALID


@pytest.mark.parametrize("claim", ["sub", "tenant_id", "entities", "azp", "roles"])
def test_missing_required_claim_is_rejected(
    validator: TokenValidator, make_token: MakeToken, claim: str
) -> None:
    assert _code(validator, make_token(**{claim: None})) is AuthErrorCode.MISSING_CLAIM


def test_claims_of_the_wrong_shape_are_rejected(
    validator: TokenValidator, make_token: MakeToken
) -> None:
    assert _code(validator, make_token(entities="HAL-DE")) is AuthErrorCode.MISSING_CLAIM
    assert _code(validator, make_token(tenant_id="")) is AuthErrorCode.MISSING_CLAIM


def test_signature_from_another_key_is_rejected(
    validator: TokenValidator, make_token: MakeToken, other_private_key: RSAPrivateKey
) -> None:
    assert _code(validator, make_token(key=other_private_key)) is AuthErrorCode.TOKEN_INVALID


def test_unknown_key_id_is_rejected(validator: TokenValidator, make_token: MakeToken) -> None:
    assert _code(validator, make_token(kid="rotated-away")) is AuthErrorCode.TOKEN_INVALID


def test_unsigned_token_is_rejected(validator: TokenValidator) -> None:
    forged = jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "sub": "x", "exp": int(NOW.timestamp()) + 60},
        key=None,  # type: ignore[arg-type]  # deliberately unsigned
        algorithm="none",
    )
    assert _code(validator, forged) is AuthErrorCode.TOKEN_INVALID


def test_hs256_signed_with_the_public_key_is_rejected(
    validator: TokenValidator, private_key: RSAPrivateKey
) -> None:
    # Classic algorithm-confusion attack: HMAC-sign using the public key bytes as the secret.
    # PyJWT refuses to build this token, so assemble it by hand.
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )

    def b64(data: bytes) -> bytes:
        return base64.urlsafe_b64encode(data).rstrip(b"=")

    header = b64(json.dumps({"alg": "HS256", "kid": KID}).encode())
    body = b64(
        json.dumps(
            {"iss": ISSUER, "aud": AUDIENCE, "sub": "x", "exp": int(NOW.timestamp()) + 60}
        ).encode()
    )
    signature = b64(hmac.new(public_pem, header + b"." + body, hashlib.sha256).digest())
    forged = (header + b"." + body + b"." + signature).decode()
    assert _code(validator, forged) is AuthErrorCode.TOKEN_INVALID


@pytest.mark.parametrize("garbage", ["", "not-a-jwt", "a.b.c", "Bearer abc"])
def test_garbage_is_rejected(validator: TokenValidator, garbage: str) -> None:
    assert _code(validator, garbage) is AuthErrorCode.TOKEN_INVALID


def test_errors_never_echo_the_token(validator: TokenValidator, make_token: MakeToken) -> None:
    token = make_token(aud="other")
    with pytest.raises(AuthError) as info:
        validator.validate(token)
    assert token not in str(info.value)
    assert token not in repr(info.value)


def test_assurance_claims_are_carried_when_present_and_absent_otherwise(
    validator: TokenValidator, make_token: MakeToken
) -> None:
    with_acr = validator.validate(make_token(acr="2", auth_time=1_700_000_000))
    assert with_acr.acr == "2" and with_acr.auth_time == 1_700_000_000
    plain = validator.validate(make_token())
    assert plain.acr is None and plain.auth_time is None
    junk = validator.validate(make_token(acr=2, auth_time="yesterday"))
    assert junk.acr is None and junk.auth_time is None  # wrong types are ignored, never trusted
