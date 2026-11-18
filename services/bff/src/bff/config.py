import os
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class BffConfig:
    issuer: str
    client_secret: str
    client_id: str = "copilot-bff"
    # The origin the browser uses. The UI dev server proxies /auth and /api here, so the browser
    # only ever talks to one origin and no CORS is needed.
    public_url: str = "http://localhost:3000"
    scopes: str = "openid"
    cookie_secure: bool = False  # True behind TLS; localhost development is plain HTTP
    session_idle_seconds: int = 1800
    session_absolute_seconds: int = 28_800
    refresh_margin_seconds: int = 60
    login_ttl_seconds: int = 600
    asks_per_minute: int = 30
    max_question_chars: int = 2000
    step_up_acr: str = "2"
    session_cookie: str = "copilot_session"
    csrf_cookie: str = "copilot_csrf"
    login_cookie: str = "copilot_login"

    @property
    def redirect_uri(self) -> str:
        return f"{self.public_url}/auth/callback"

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "BffConfig":
        keycloak = env.get("KEYCLOAK_URL", "http://localhost:8081")
        return cls(
            issuer=f"{keycloak}/realms/halcyon",
            client_secret=env["BFF_CLIENT_SECRET"],
            public_url=env.get("PUBLIC_URL", "http://localhost:3000"),
            cookie_secure=env.get("COOKIE_SECURE", "false").lower() == "true",
        )
