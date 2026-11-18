from collections.abc import Mapping
from typing import Any, Protocol

import jwt

from copilot_auth.errors import AuthError, AuthErrorCode


class KeyProvider(Protocol):
    def get(self, kid: str) -> Any | None:  # a cryptography public key object
        """Return the verification key for `kid`, or None if unknown."""


class StaticKeyProvider:
    """Fixed keys, for tests and offline use."""

    def __init__(self, keys: Mapping[str, Any]) -> None:
        self._keys = dict(keys)

    def get(self, kid: str) -> Any | None:
        return self._keys.get(kid)


class JwksKeyProvider:
    """Keys from the IdP's JWKS endpoint, cached, with a hard timeout on every fetch."""

    def __init__(self, jwks_url: str, *, timeout_seconds: float = 3.0) -> None:
        self._client = jwt.PyJWKClient(jwks_url, cache_keys=True, timeout=timeout_seconds)

    def get(self, kid: str) -> Any | None:
        try:
            return self._client.get_signing_key(kid).key
        except jwt.PyJWKClientConnectionError as exc:
            raise AuthError(AuthErrorCode.IDP_UNAVAILABLE) from exc
        except jwt.PyJWTError:
            return None
