"""Nextcloud ↔ Kanidm (user_oidc) and Nextcloud Mail ↔ Stalwart (Nextcloud path).

Sidecars under .nextcloud-easy-deploy/integration/:
- oidc-provider.yaml: user_oidc provider. The client secret is a reference to
  the file Kanidm writes when it applies, so the Nextcloud kit reads it then.
- mail.yaml: Nextcloud Mail provisioning against Stalwart (IMAP, SMTP, Sieve).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts.integrations.base import IdentityConsumer, MailConsumer, MailServer, WireContext
from scripts.integrations.common import (
    KANIDM_SSO_NAME,
    consumer_should_skip,
    deploy_mapping,
    kanidm_discovery_url,
    kanidm_secret_path,
    managed_is_false,
    read_required_domain,
    secret_ref,
    write_sidecar,
)

NEXTCLOUD_STATE = Path(".nextcloud-easy-deploy")
PROVIDER_SIDECAR = NEXTCLOUD_STATE / "integration" / "oidc-provider.yaml"
MAIL_SIDECAR = NEXTCLOUD_STATE / "integration" / "mail.yaml"
DEFAULT_CLIENT_ID = "nextcloud"
REDIRECT_PATHS = ("/apps/user_oidc/code", "/index.php/apps/user_oidc/code")
OIDC_SCOPES = ["openid", "profile", "email"]
# Kanidm emits this claim for members of ADMIN_GROUP; user_oidc provisions it
# into Nextcloud's built-in "admin" group (and only that group).
GROUPS_CLAIM = "nextcloud_groups"
ADMIN_GROUP = "nextcloud-admins"
NEXTCLOUD_ADMIN_GID = "admin"


def build_nextcloud_client(domain: str, *, client_id: str = DEFAULT_CLIENT_ID) -> dict[str, Any]:
    origin = f"https://{domain.strip().rstrip('/')}"
    return {
        "client_id": client_id,
        "client_name": "Nextcloud",
        "public": False,
        "prefer_short_username": True,
        "landing_url": f"{origin}/",
        "redirect_uris": [origin + path for path in REDIRECT_PATHS],
        "scopes": list(OIDC_SCOPES),
        "claim_maps": [
            {
                "claim": GROUPS_CLAIM,
                "join": "array",
                "mappings": [{"group": ADMIN_GROUP, "values": [NEXTCLOUD_ADMIN_GID]}],
            }
        ],
        # Kanidm creates these groups and puts the first operator account in
        # operator_groups, so the person who ran the wizard is a Nextcloud admin.
        "groups": [ADMIN_GROUP],
        "operator_groups": [ADMIN_GROUP],
    }


def build_nextcloud_provider(
    kanidm_domain: str,
    *,
    client_id: str = DEFAULT_CLIENT_ID,
    secret_file: Path,
) -> dict[str, Any]:
    host = kanidm_domain.strip().rstrip("/")
    return {
        "provider": "kanidm",
        "name": KANIDM_SSO_NAME,
        "domain": host,
        "account_url": f"https://{host}/",
        "discovery_url": kanidm_discovery_url(host, client_id),
        "client_id": client_id,
        "client_secret": secret_ref(secret_file, "client_secret"),
        "scopes": " ".join(OIDC_SCOPES),
        "mapping": {
            "uid": "preferred_username",
            "display_name": "name",
            "email": "email",
            "groups": GROUPS_CLAIM,
        },
        "group_whitelist_regex": f"^{NEXTCLOUD_ADMIN_GID}$",
    }


def build_nextcloud_mail(server: MailServer) -> dict[str, Any]:
    return {
        "provider": "stalwart",
        "email_domain": server.email_domain,
        "imap": {"host": server.hostname, "port": server.imap_port, "ssl": "ssl"},
        "smtp": {"host": server.hostname, "port": server.submission_port, "ssl": "ssl"},
        "sieve": {"host": server.hostname, "port": server.sieve_port, "ssl": "tls"},
        "master_user": server.master_user,
        "master_user_separator": server.master_user_separator,
        "master_password": dict(server.master_password),
    }


def read_nextcloud_domain(kit_root: Path) -> str:
    return read_required_domain(kit_root, "Nextcloud", "nextcloud", "domain", "cloud.example.com")


class NextcloudConsumer(IdentityConsumer):
    kit = "nextcloud"
    label = "Nextcloud"
    default_client_id = DEFAULT_CLIENT_ID
    skip_note = "Nextcloud OIDC wiring skipped (auth.oidc.managed: false or non-Kanidm provider)."

    def should_skip(self, kit_root: Path, service: dict) -> bool:
        return consumer_should_skip(kit_root, service)

    def wire_local(self, ctx: WireContext, kit_root: Path) -> list[str]:
        client_id = self.client_id(ctx)
        domain = str(ctx.consumer_cfg(self.kit).get("domain") or "").strip() or read_nextcloud_domain(kit_root)
        client_path = ctx.write_client(build_nextcloud_client(domain, client_id=client_id))
        provider_path = kit_root / PROVIDER_SIDECAR
        write_sidecar(
            provider_path,
            build_nextcloud_provider(
                ctx.kanidm_domain,
                client_id=client_id,
                secret_file=kanidm_secret_path(ctx.kanidm_root, client_id),
            ),
        )
        return [
            f"Wired Nextcloud ({domain}) → Kanidm ({ctx.kanidm_domain}) as client {client_id!r}.",
            f"  Kanidm client definition: {client_path}",
            f"  Nextcloud provider definition: {provider_path}",
        ]

    def wire_remote(self, ctx: WireContext) -> list[str] | None:
        domain = str(ctx.consumer_cfg(self.kit).get("domain") or "").strip()
        if not domain:
            return None
        client_id = self.client_id(ctx)
        ctx.write_client(build_nextcloud_client(domain, client_id=client_id))
        return [
            f"Wired remote Nextcloud ({domain}) client {client_id!r} into Kanidm "
            f"(split-VPS). Configure the Nextcloud OIDC provider on the other host."
        ]


class NextcloudMail(MailConsumer):
    kit = "nextcloud"
    label = "Nextcloud Mail"

    def should_skip(self, kit_root: Path) -> bool:
        return managed_is_false(deploy_mapping(kit_root, "mail"))

    def wire(self, kit_root: Path, server: MailServer) -> list[str]:
        path = kit_root / MAIL_SIDECAR
        write_sidecar(path, build_nextcloud_mail(server))
        return [
            f"Wired Nextcloud Mail → Stalwart ({server.hostname}) for @{server.email_domain} mailboxes.",
            f"  Mail provisioning definition: {path}",
        ]

    def unwire(self, kit_root: Path) -> list[str]:
        path = kit_root / MAIL_SIDECAR
        if not path.is_file():
            return []
        path.unlink()
        return [f"Removed {path} (Stalwart is not enabled)."]
