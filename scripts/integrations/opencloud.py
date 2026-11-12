"""OpenCloud ↔ Kanidm (OpenCloud + Matrix path)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts.integrations.base import IdentityConsumer, WireContext
from scripts.integrations.common import (
    consumer_should_skip,
    kanidm_issuer_url,
    read_required_domain,
    write_sidecar,
)

OPENCLOUD_REDIRECT_PATHS = (
    "/",
    "/web-oidc-callback",
    "/oidc-callback.html",
    "/oidc-silent-redirect.html",
)
# OpenCloud's web client requests `groups` as well as `groups_name`. Kanidm
# denies the whole grant if any requested scope is missing from the scope map.
DEFAULT_OIDC_SCOPES = ["openid", "profile", "email", "groups", "groups_name"]
OPENCLOUD_ROLE_CLAIM = "opencloudRoles"
PROVIDER_SIDECAR = Path(".opencloud-easy-deploy") / "integration" / "oidc-provider.yaml"


def build_opencloud_client(
    domain: str,
    *,
    client_id: str = "opencloud",
) -> dict[str, Any]:
    origin = f"https://{domain.rstrip('/')}"
    return {
        "client_id": client_id,
        "client_name": "OpenCloud",
        "public": True,
        "prefer_short_username": True,
        "landing_url": origin,
        "redirect_uris": [origin + path if path != "/" else origin + "/" for path in OPENCLOUD_REDIRECT_PATHS],
        "scopes": list(DEFAULT_OIDC_SCOPES),
        "claim_maps": [
            {
                "claim": OPENCLOUD_ROLE_CLAIM,
                "join": "array",
                "mappings": [
                    {"group": "opencloud-admin", "values": ["admin"]},
                    {"group": "opencloud-user", "values": ["user"]},
                    {"group": "opencloud-guest", "values": ["guest"]},
                ],
            }
        ],
        # Kanidm creates these groups; the operator who ran the wizard can use and run OpenCloud.
        "groups": ["opencloud-admin", "opencloud-user", "opencloud-guest"],
        "operator_groups": ["opencloud-admin", "opencloud-user"],
    }


def build_opencloud_provider(
    kanidm_domain: str,
    *,
    client_id: str = "opencloud",
) -> dict[str, Any]:
    host = kanidm_domain.strip().rstrip("/")
    return {
        "provider": "kanidm",
        "issuer_url": kanidm_issuer_url(host, client_id),
        "account_url": f"https://{host}/",
        "domain": host,
        "client_id": client_id,
        "client_scopes": " ".join(DEFAULT_OIDC_SCOPES),
        "role_claim": OPENCLOUD_ROLE_CLAIM,
        "role_mapping": {
            "admin": "admin",
            "user": "user",
            "guest": "guest",
        },
    }


def read_opencloud_domain(kit_root: Path) -> str:
    return read_required_domain(kit_root, "OpenCloud", "opencloud", "domain", "cloud.example.com")


class OpenCloudConsumer(IdentityConsumer):
    kit = "opencloud"
    label = "OpenCloud"
    default_client_id = "opencloud"
    skip_note = "OpenCloud OIDC wiring skipped (auth.oidc.managed: false or non-Kanidm provider)."

    def should_skip(self, kit_root: Path, service: dict) -> bool:
        return consumer_should_skip(kit_root, service)

    def wire_local(self, ctx: WireContext, kit_root: Path) -> list[str]:
        client_id = self.client_id(ctx)
        domain = str(ctx.consumer_cfg(self.kit).get("domain") or "").strip() or read_opencloud_domain(kit_root)
        client_path = ctx.write_client(build_opencloud_client(domain, client_id=client_id))
        provider_path = kit_root / PROVIDER_SIDECAR
        write_sidecar(provider_path, build_opencloud_provider(ctx.kanidm_domain, client_id=client_id))
        return [
            f"Wired OpenCloud ({domain}) → Kanidm ({ctx.kanidm_domain}) as client {client_id!r}.",
            f"  Kanidm client definition: {client_path}",
            f"  OpenCloud provider definition: {provider_path}",
        ]

    def wire_remote(self, ctx: WireContext) -> list[str] | None:
        domain = str(ctx.consumer_cfg(self.kit).get("domain") or "").strip()
        if not domain:
            return None
        client_id = self.client_id(ctx)
        ctx.write_client(build_opencloud_client(domain, client_id=client_id))
        return [
            f"Wired remote OpenCloud ({domain}) client {client_id!r} into Kanidm "
            f"(split-VPS). Configure OpenCloud auth.oidc on the other host."
        ]
