"""PKCE (RFC 7636) and random tokens."""

import base64
import hashlib
import secrets


def new_verifier() -> str:
    return secrets.token_urlsafe(48)  # 64 URL-safe characters, within the 43 to 128 allowed


def challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def random_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)
