from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import jwt
from pydantic import BaseModel, ConfigDict

from copilot_auth.errors import AuthError, AuthErrorCode
from copilot_auth.keys import KeyProvider

# One algorithm, pinned. Accepting what the token header asks for enables algorithm confusion.
_ALGORITHM = "RS256"


class Principal(BaseModel):
    """The caller as established by a verified, audience-bound token."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject: str
    tenant_id: str
    entities: tuple[str, ...]
    roles: tuple[str, ...]
    authorized_party: str
    # How strongly and how recently the person authenticated. Absent when the IdP did not say,
    # which sensitive actions must treat as 'not strong enough'.
    acr: str | None = None
    auth_time: int | None = None


def _string(claims: dict[str, Any], name: str) -> str:
    value = claims.get(name)
    if not isinstance(value, str) or not value:
        raise AuthError(AuthErrorCode.MISSING_CLAIM)
    return value


def _strings(claims: dict[str, Any], name: str) -> tuple[str, ...]:
    value = claims.get(name)
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise AuthError(AuthErrorCode.MISSING_CLAIM)
    return tuple(value)


def _auth_time(claims: dict[str, Any]) -> int | None:
    value = claims.get("auth_time")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


class TokenValidator:
    """Validates one service's inbound tokens. No pass-through: each hop exchanges for the next."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        keys: KeyProvider,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._issuer = issuer
        self._audience = audience
        self._keys = keys
        self._clock = clock

    def validate(self, token: str) -> Principal:
        claims = self._verified_claims(token)
        if claims.get("iss") != self._issuer:
            raise AuthError(AuthErrorCode.WRONG_ISSUER)
        audience = claims.get("aud")
        audiences = [audience] if isinstance(audience, str) else audience
        if not isinstance(audiences, list) or self._audience not in audiences:
            raise AuthError(AuthErrorCode.WRONG_AUDIENCE)
        expires = claims.get("exp")
        if isinstance(expires, bool) or not isinstance(expires, int | float):
            raise AuthError(AuthErrorCode.TOKEN_INVALID)
        now = self._clock().timestamp()
        if expires <= now:  # exp is exclusive
            raise AuthError(AuthErrorCode.TOKEN_EXPIRED)
        not_before = claims.get("nbf")
        if not_before is not None and (
            isinstance(not_before, bool)
            or not isinstance(not_before, int | float)
            or not_before > now
        ):
            raise AuthError(AuthErrorCode.TOKEN_INVALID)
        return Principal(
            subject=_string(claims, "sub"),
            tenant_id=_string(claims, "tenant_id"),
            entities=_strings(claims, "entities"),
            roles=_strings(claims, "roles"),
            authorized_party=_string(claims, "azp"),
            acr=claims["acr"] if isinstance(claims.get("acr"), str) else None,
            auth_time=_auth_time(claims),
        )

    def _verified_claims(self, token: str) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")
            if header.get("alg") != _ALGORITHM or not isinstance(kid, str):
                raise AuthError(AuthErrorCode.TOKEN_INVALID)
            key = self._keys.get(kid)
            if key is None:
                raise AuthError(AuthErrorCode.TOKEN_INVALID)
            # Signature only: issuer, audience, expiry and nbf are checked here with our own clock
            # and mapped to distinct codes.
            return jwt.decode(
                token,
                key,
                algorithms=[_ALGORITHM],
                options={
                    "verify_exp": False,
                    "verify_nbf": False,
                    "verify_iat": False,
                    "verify_aud": False,
                    "verify_iss": False,
                    "require": ["exp"],
                },
            )
        except jwt.PyJWTError as exc:
            raise AuthError(AuthErrorCode.TOKEN_INVALID) from exc
