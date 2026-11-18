from collections.abc import Callable

import pytest
from copilot_auth.testing import mint_token
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey


@pytest.fixture(scope="session")
def private_key() -> RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="session")
def other_private_key() -> RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def make_token(private_key: RSAPrivateKey) -> Callable[..., str]:
    def build(*, key: RSAPrivateKey | None = None, **overrides: object) -> str:
        return mint_token(key or private_key, **overrides)  # type: ignore[arg-type]

    return build
