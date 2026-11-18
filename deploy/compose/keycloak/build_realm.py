"""Generate realm.json for the Halcyon Keycloak realm (ADR 0003).

Secrets are `${ENV}` placeholders that Keycloak substitutes at import, so none are committed.
Run: python3 deploy/compose/keycloak/build_realm.py > deploy/compose/keycloak/realm.json
"""

import json
from pathlib import Path

REALM = "halcyon"

# Resource servers and exchanging clients, one audience per hop (ARCHITECTURE.md §5.1).
SERVICE_CLIENTS = {
    "orchestrator": "ORCHESTRATOR_CLIENT_SECRET",
    "mcp-analytics": "MCP_ANALYTICS_CLIENT_SECRET",
    "query-api": "QUERY_API_CLIENT_SECRET",
    "approval-service": "APPROVAL_CLIENT_SECRET",
}

# One source of truth for who exists and what they may see. The Query API provisions its
# database roles from the same file, and a test compares both sides (ARCHITECTURE.md §8.3).
IDENTITY = json.loads((Path(__file__).parent.parent / "identity" / "users.json").read_text())
TENANT = IDENTITY["tenant_id"]
USERS = [
    (u["id"], u["username"], u["first_name"], u["last_name"], u["roles"], u["entities"])
    for u in IDENTITY["users"]
]

user_profile = {
    "attributes": [
        {
            "name": "username",
            "displayName": "${username}",
            "validations": {"length": {"min": 3, "max": 255}},
        },
        {"name": "email", "displayName": "${email}", "validations": {"email": {}}},
        {"name": "firstName", "displayName": "${firstName}", "validations": {}},
        {"name": "lastName", "displayName": "${lastName}", "validations": {}},
        # Only administrators may set scope attributes; users cannot edit their own.
        {"name": "tenant_id", "permissions": {"view": ["admin"], "edit": ["admin"]}},
        {
            "name": "entities",
            "multivalued": True,
            "permissions": {"view": ["admin"], "edit": ["admin"]},
        },
    ]
}


def mapper(name: str, protocol_mapper: str, config: dict[str, str]) -> dict[str, object]:
    return {
        "name": name,
        "protocol": "openid-connect",
        "protocolMapper": protocol_mapper,
        "consentRequired": False,
        "config": config,
    }


claims_scope = {
    "name": "copilot-claims",
    "protocol": "openid-connect",
    "attributes": {"include.in.token.scope": "false", "display.on.consent.screen": "false"},
    "protocolMappers": [
        # Supplying our own clientScopes drops Keycloak's built-in `basic` scope, so emit sub here.
        mapper("sub", "oidc-sub-mapper", {"access.token.claim": "true"}),
        mapper(
            "tenant_id",
            "oidc-usermodel-attribute-mapper",
            {
                "user.attribute": "tenant_id",
                "claim.name": "tenant_id",
                "jsonType.label": "String",
                "access.token.claim": "true",
            },
        ),
        mapper(
            "entities",
            "oidc-usermodel-attribute-mapper",
            {
                "user.attribute": "entities",
                "claim.name": "entities",
                "jsonType.label": "String",
                "multivalued": "true",
                "access.token.claim": "true",
            },
        ),
        mapper(
            "roles",
            "oidc-usermodel-realm-role-mapper",
            {
                "claim.name": "roles",
                "jsonType.label": "String",
                "multivalued": "true",
                "access.token.claim": "true",
            },
        ),
    ],
}


# Which client may exchange a token for which audience. Anything else is refused by Keycloak.
EXCHANGES = {
    "copilot-bff": ["orchestrator", "approval-service"],
    "orchestrator": ["mcp-analytics"],
    "mcp-analytics": ["query-api"],
}

audience_scopes = [
    {
        "name": f"aud-{aud}",
        "protocol": "openid-connect",
        "attributes": {"include.in.token.scope": "false", "display.on.consent.screen": "false"},
        "protocolMappers": [
            mapper(
                f"aud-{aud}",
                "oidc-audience-mapper",
                {
                    "included.client.audience": aud,
                    "access.token.claim": "true",
                    "id.token.claim": "false",
                },
            )
        ],
    }
    for aud in sorted({a for auds in EXCHANGES.values() for a in auds})
]


def audience_mapper(audience: str) -> dict[str, object]:
    return mapper(
        f"aud-{audience}",
        "oidc-audience-mapper",
        {
            "included.client.audience": audience,
            "access.token.claim": "true",
            "id.token.claim": "false",
        },
    )


def client(client_id: str, secret_env: str | None, **extra: object) -> dict[str, object]:
    base: dict[str, object] = {
        "clientId": client_id,
        "enabled": True,
        "protocol": "openid-connect",
        "publicClient": secret_env is None,
        "standardFlowEnabled": False,
        "implicitFlowEnabled": False,
        "directAccessGrantsEnabled": False,
        "serviceAccountsEnabled": False,
        "fullScopeAllowed": False,
        "defaultClientScopes": ["copilot-claims"],
        "optionalClientScopes": [],
    }
    if secret_env:
        base["secret"] = "${" + secret_env + "}"
    base.update(extra)
    return base


clients = [
    client(
        "copilot-bff",
        "BFF_CLIENT_SECRET",
        standardFlowEnabled=True,
        redirectUris=["http://localhost:3000/auth/callback"],
        webOrigins=["http://localhost:3000"],
        attributes={
            "pkce.code.challenge.method": "S256",
            "standard.token.exchange.enabled": "true",
            "post.logout.redirect.uris": "http://localhost:3000/",
        },
        protocolMappers=[audience_mapper("copilot-bff")],
    ),
    # Test-only: lets integration tests obtain a user token without a browser. Never deploy.
    client(
        "test-harness",
        None,
        directAccessGrantsEnabled=True,
        protocolMappers=[audience_mapper("copilot-bff")],
    ),
]
for cid, env in SERVICE_CLIENTS.items():
    clients.append(client(cid, env, attributes={"standard.token.exchange.enabled": "true"}))
for c in clients:
    c["optionalClientScopes"] = [f"aud-{a}" for a in EXCHANGES.get(str(c["clientId"]), [])]

users = []
for user_id, username, first, last, roles, entities in USERS:
    users.append(
        {
            "id": user_id,
            "username": username,
            "firstName": first,
            "lastName": last,
            "email": f"{username}@halcyon.example",
            "emailVerified": True,
            "enabled": True,
            "attributes": {"tenant_id": [TENANT], "entities": entities},
            "realmRoles": roles,
            "credentials": [
                {"type": "password", "value": "${DEMO_USER_PASSWORD}", "temporary": False}
            ],
        }
    )

realm = {
    "realm": REALM,
    "enabled": True,
    "sslRequired": "external",
    "registrationAllowed": False,
    "bruteForceProtected": True,
    "accessTokenLifespan": 300,
    "ssoSessionIdleTimeout": 1800,
    "roles": {"realm": [{"name": r} for r in ("analyst", "treasury_manager", "approver")]},
    "clientScopes": [claims_scope, *audience_scopes],
    # Without a scope mapping the roles mapper emits nothing for clients with fullScope off.
    "scopeMappings": [
        {"clientScope": "copilot-claims", "roles": ["analyst", "treasury_manager", "approver"]}
    ],
    "defaultDefaultClientScopes": [],
    "clients": clients,
    "users": users,
    "components": {
        "org.keycloak.userprofile.UserProfileProvider": [
            {
                "providerId": "declarative-user-profile",
                "config": {"kc.user.profile.config": [json.dumps(user_profile)]},
            }
        ]
    },
}

print(json.dumps(realm, indent=2))
