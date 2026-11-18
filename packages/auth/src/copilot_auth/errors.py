# ruff: noqa: S105  # enum values are error codes, not passwords
from enum import StrEnum


class AuthErrorCode(StrEnum):
    """Mapped codes shown to callers. Raw IdP and JWT error text never leaves this package."""

    TOKEN_INVALID = "token_invalid"
    TOKEN_EXPIRED = "token_expired"
    WRONG_ISSUER = "wrong_issuer"
    WRONG_AUDIENCE = "wrong_audience"
    MISSING_CLAIM = "missing_claim"
    EXCHANGE_DENIED = "exchange_denied"
    IDP_UNAVAILABLE = "idp_unavailable"


class AuthError(Exception):
    def __init__(self, code: AuthErrorCode) -> None:
        super().__init__(code.value)
        self.code = code
