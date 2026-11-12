"""Stalwart (and Bulwark webmail, when deployed) ↔ Kanidm. Core kit on every path."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts.integrations.base import IdentityConsumer, MailServer, WireContext
from scripts.integrations.common import (
    KANIDM_STATE,
    deploy_mapping,
    kanidm_issuer_url,
    ldap_base_dn,
    load_yaml,
    managed_is_false,
    secret_ref,
    to_bool,
    write_sidecar,
)

STALWART_STATE = ".stalwart-easy-deploy"
# Keep in sync with stalwart-easy-deploy/scripts/groupware.py impersonation_username.
MASTER_USER_SEPARATOR = "%"
IDENTITY_SIDECAR = Path(STALWART_STATE) / "integration" / "identity-provider.yaml"
STALWART_LDAP_FILTER_LOGIN = (
    "(&(|(objectclass=account)(objectclass=person))(|(name=?)(uid=?)(mail=?)(spn=?)))"
)
STALWART_LDAP_FILTER_MAILBOX = (
    "(&(|(objectclass=account)(objectclass=person))(|(mail=?)(spn=?)))"
)
MAIL_USERS_GROUP = "mail-users"

BULWARK_CALLBACK_LOCALES = (
    "en",
    "de",
    "da",
    "nl",
    "fr",
    "es",
    "it",
    "pt",
    "sv",
    "pl",
    "cs",
    "ja",
    "zh",
    "ru",
)


def stalwart_mailbox_filter(kanidm_domain: str, group: str = MAIL_USERS_GROUP) -> str:
    """Only mail-users members are Stalwart mailboxes. Keep in sync with stalwart mailboxes.py."""
    domain = kanidm_domain.strip().lower().rstrip(".")
    group_dn = f"spn={group}@{domain},{ldap_base_dn(domain)}"
    return f"(&(|(objectclass=account)(objectclass=person))(mail=?)(memberof={group_dn}))"


def build_stalwart_client(
    mail_hostname: str,
    *,
    client_id: str = "stalwart-webui",
    webmail_hostname: str = "",
) -> dict[str, Any]:
    mail = f"https://{mail_hostname.rstrip('/')}"
    webmail = f"https://{webmail_hostname.rstrip('/')}" if webmail_hostname.strip() else ""
    landing = webmail or mail
    # WebUI 1.x (the bundle Stalwart 0.16 serves) sends the browser to
    # {mount}/oauth/callback and back to {mount}/login. The mount is /admin
    # for the server console and /account for the user portal.
    redirects = [
        f"{landing}/",
        f"{landing}/login",
        f"{mail}/",
        f"{mail}/login",
        f"{mail}/admin",
        f"{mail}/admin/login",
        f"{mail}/admin/oauth/callback",
        f"{mail}/account/login",
        f"{mail}/account/oauth/callback",
        f"{mail}/auth/oidc/callback",
    ]
    if webmail:
        redirects.extend(
            [
                f"{webmail}/auth/callback",
                *[f"{webmail}/{locale}/auth/callback" for locale in BULWARK_CALLBACK_LOCALES],
            ]
        )
    return {
        "client_id": client_id,
        "client_name": "Webmail" if webmail else "Stalwart",
        "public": True,
        "prefer_short_username": True,
        "landing_url": landing,
        "redirect_uris": list(dict.fromkeys(redirects)),
        "scopes": ["openid", "profile", "email"],
        # Webmail sign-in materialises a mailbox. Only people whose mail has moved.
        "scope_group": MAIL_USERS_GROUP,
        # Kanidm creates the group; the operator who ran the wizard gets a mailbox.
        "groups": [MAIL_USERS_GROUP],
        "operator_groups": [MAIL_USERS_GROUP],
    }


def build_stalwart_identity(
    kanidm_domain: str,
    mail_domain: str,
    *,
    client_id: str = "stalwart-webui",
    ldap_token: str = "",
) -> dict[str, Any]:
    host = kanidm_domain.strip().rstrip("/")
    identity = {
        "provider": "kanidm",
        "domain": host,
        "account_url": f"https://{host}/",
        "oidc": {
            "issuer_url": kanidm_issuer_url(host, client_id),
            "client_id": client_id,
            "claim_username": "preferred_username",
            "username_domain": mail_domain,
            "claim_name": "name",
            "claim_groups": "groups",
            "require_audience": client_id,
        },
        "auth_directory": "oidc",
        "ldap": {
            "url": "ldaps://kanidm:3636",
            "base_dn": ldap_base_dn(host),
            "bind_dn": "dn=token",
            "bind_authentication": True,
            "allow_invalid_certs": True,
            "use_tls": True,
            "filter_login": STALWART_LDAP_FILTER_LOGIN,
            "filter_mailbox": stalwart_mailbox_filter(host),
            "attr_email": "mail",
            "attr_description": "displayname",
            "attr_member_of": "memberof",
            "group_class": "group",
        },
    }
    if ldap_token:
        identity["ldap"]["bind_secret"] = ldap_token
    return identity


def read_stalwart_domains(kit_root: Path) -> tuple[str, str]:
    deploy = kit_root / "deploy.yaml"
    if not deploy.is_file():
        raise FileNotFoundError(f"Stalwart deploy.yaml not found at {deploy}")
    data = load_yaml(deploy)
    stalwart = data.get("stalwart") or {}
    hostname = str(stalwart.get("hostname") or "").strip()
    domain = str(stalwart.get("domain") or "").strip()
    if not hostname or hostname == "mail.example.com":
        raise ValueError(f"{deploy}: stalwart.hostname is not set")
    if not domain or domain == "example.com":
        raise ValueError(f"{deploy}: stalwart.domain is not set")
    return hostname, domain


def read_webmail_domain(kit_root: Path) -> str:
    deploy = kit_root / "deploy.yaml"
    if not deploy.is_file():
        return ""
    bulwark = (load_yaml(deploy) or {}).get("bulwark") or {}
    if managed_is_false(bulwark) or not to_bool(bulwark.get("enabled", True)):
        return ""
    domain = str(bulwark.get("domain") or "").strip()
    if domain in {"", "webmail.example.com"}:
        return ""
    return domain


def read_mail_server(kit_root: Path) -> MailServer:
    """Stalwart as seen by a webmail client on this VPS.

    Mail ports are published on the host and serve Caddy's certificate for the
    mail hostname, so clients connect to the public hostname. The recovery
    admin is a fallback admin and may log in as any mailbox ("box%admin").
    """
    hostname, domain = read_stalwart_domains(kit_root)
    data = load_yaml(kit_root / "deploy.yaml")
    stalwart = data.get("stalwart") or {}
    ports = data.get("ports") or {}
    master_user = str(stalwart.get("recovery_admin_user") or "admin").strip() or "admin"
    return MailServer(
        hostname=hostname,
        email_domain=domain,
        imap_port=int(ports.get("imaps") or 993),
        submission_port=int(ports.get("submissions") or 465),
        sieve_port=int(ports.get("sieve") or 4190),
        master_user=master_user,
        master_user_separator=MASTER_USER_SEPARATOR,
        master_password=secret_ref(kit_root / STALWART_STATE / "secrets.yaml", "RECOVERY_ADMIN_PASSWORD"),
    )


def read_ldap_token(kanidm_root: Path) -> str:
    secrets_path = kanidm_root / KANIDM_STATE / "secrets.yaml"
    if not secrets_path.is_file():
        return ""
    data = load_yaml(secrets_path)
    return str(data.get("LDAP_TOKEN") or "").strip()


def stalwart_should_skip(kit_root: Path, service_entry: dict) -> bool:
    if managed_is_false(service_entry.get("identity") if isinstance(service_entry.get("identity"), dict) else {}):
        return True
    identity = deploy_mapping(kit_root, "identity")
    if managed_is_false(identity):
        return True
    provider = str(identity.get("provider") or "").strip().lower()
    return bool(provider) and provider != "kanidm"


class StalwartConsumer(IdentityConsumer):
    kit = "stalwart"
    label = "Stalwart"
    default_client_id = "stalwart-webui"
    skip_note = "Stalwart identity wiring skipped (identity.managed: false or non-Kanidm provider)."

    def should_skip(self, kit_root: Path, service: dict) -> bool:
        return stalwart_should_skip(kit_root, service)

    def _host_override(self, ctx: WireContext) -> str:
        cfg = ctx.consumer_cfg(self.kit)
        return str(cfg.get("hostname") or cfg.get("domain") or "").strip()

    def _webmail_override(self, ctx: WireContext) -> str:
        cfg = ctx.consumer_cfg(self.kit)
        return str(cfg.get("webmail") or cfg.get("webmail_domain") or "").strip()

    def wire_local(self, ctx: WireContext, kit_root: Path) -> list[str]:
        cfg = ctx.consumer_cfg(self.kit)
        client_id = self.client_id(ctx)
        hostname, mail_domain = read_stalwart_domains(kit_root)
        hostname = self._host_override(ctx) or hostname
        mail_domain = str(cfg.get("mail_domain") or "").strip() or mail_domain
        webmail = self._webmail_override(ctx) or read_webmail_domain(kit_root)
        ctx.write_client(build_stalwart_client(hostname, client_id=client_id, webmail_hostname=webmail))
        stale_client = ctx.clients_dir / "stalwart.yaml"
        if client_id != "stalwart" and stale_client.is_file():
            stale_client.unlink()
        write_sidecar(
            kit_root / IDENTITY_SIDECAR,
            build_stalwart_identity(
                ctx.kanidm_domain,
                mail_domain,
                client_id=client_id,
                ldap_token=read_ldap_token(ctx.kanidm_root),
            ),
        )
        target = webmail or hostname
        return [
            f"Wired Stalwart/Webmail ({target}) → Kanidm LDAP + OIDC ({ctx.kanidm_domain}) "
            f"as client {client_id!r}."
        ]

    def wire_remote(self, ctx: WireContext) -> list[str] | None:
        hostname = self._host_override(ctx)
        if not hostname:
            return None
        client_id = self.client_id(ctx)
        ctx.write_client(
            build_stalwart_client(hostname, client_id=client_id, webmail_hostname=self._webmail_override(ctx))
        )
        return [
            f"Wired remote Stalwart ({hostname}) client {client_id!r} into Kanidm "
            f"(split-VPS). Configure Stalwart identity on the other host."
        ]
