import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    issuer: str
    jwks_url: str
    dsn: str  # the login role: it holds no data grants and can only SET ROLE to a user role
    audience: str = "query-api"
    database_name: str = "treasury_analytics"
    pool_max_size: int = 5

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            issuer=os.environ["OIDC_ISSUER"],
            jwks_url=os.environ["OIDC_JWKS_URL"],
            dsn=os.environ["QUERY_DB_DSN"],
        )
