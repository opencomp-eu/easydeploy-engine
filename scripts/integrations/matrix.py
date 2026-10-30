"""Matrix (MAS) ↔ Kanidm (OpenCloud + Matrix path)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from scripts.integrations.base import IdentityConsumer, WireContext
from scripts.integrations.common import (
    KANIDM_SSO_NAME,
    LOCAL_IDPS,
    deploy_mapping,
    kanidm_issuer_url,
    kanidm_secret_path,
    load_yaml,
    managed_is_false,
    plaintext_oidc_secret,
    read_required_domain,
    to_bool,
    write_sidecar,
)

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
MAS_PATH_PREFIX = "/auth"
DEFAULT_MATRIX_CLIENT_ID = "matrix"
PROVIDER_SIDECAR = Path(".matrix-easy-deploy") / "integration" / "oidc-provider.yaml"


def stable_provider_ulid(name: str, issuer: str) -> str:
    """Must match matrix-easy-deploy/scripts/mas_config.py stable_provider_ulid."""
    digest = hashlib.sha256(f"{name}\0{issuer}".encode()).digest()
    value = int.from_bytes(digest[:16], byteorder="big")
    chars: list[str] = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def matrix_kanidm_provider_id(kanidm_domain: str, client_id: str = DEFAULT_MATRIX_CLIENT_ID) -> str:
    return stable_provider_ulid(KANIDM_SSO_NAME, kanidm_issuer_url(kanidm_domain, client_id))


def matrix_kanidm_redirect_uri(
    matrix_domain: str,
    kanidm_domain: str,
    client_id: str = DEFAULT_MATRIX_CLIENT_ID,
) -> str:
    provider_id = matrix_kanidm_provider_id(kanidm_domain, client_id)
    return f"https://{matrix_domain.rstrip('/')}{MAS_PATH_PREFIX}/upstream/callback/{provider_id}"


def load_existing_matrix_secret(matrix_sidecar: Path, kanidm_secret: Path) -> str:
    for path in (matrix_sidecar, kanidm_secret):
        if not path.is_file():
            continue
        data = load_yaml(path)
        secret = plaintext_oidc_secret(str(data.get("client_secret") or ""))
        if secret and not secret.startswith("$"):
            return secret
    return ""


def build_matrix_client(
    matrix_domain: str,
    kanidm_domain: str,
    *,
    client_id: str = DEFAULT_MATRIX_CLIENT_ID,
    element_domain: str = "",
) -> dict[str, Any]:
    app_host = str(element_domain or matrix_domain).strip().rstrip("/")
    app = f"https://{app_host}"
    client = {
        "client_id": client_id,
        "client_name": "Matrix",
        "public": False,
        "prefer_short_username": True,
        "landing_url": app,
        "redirect_uris": [matrix_kanidm_redirect_uri(matrix_domain, kanidm_domain, client_id)],
        "scopes": ["openid", "profile", "email"],
    }
    if str(element_domain or "").strip():
        client["image"] = f"{app}/vector-icons/144.png"
    return client


def build_matrix_provider(
    kanidm_domain: str,
    *,
    client_id: str = DEFAULT_MATRIX_CLIENT_ID,
    client_secret: str = "",
) -> dict[str, Any]:
    issuer = kanidm_issuer_url(kanidm_domain, client_id)
    provider = {
        "provider": "kanidm",
        "name": KANIDM_SSO_NAME,
        "issuer": issuer,
        "client_id": client_id,
        "id": matrix_kanidm_provider_id(kanidm_domain, client_id),
        "allow_registration": True,
        "scopes": ["openid", "profile", "email"],
    }
    if client_secret:
        provider["client_secret"] = client_secret
    return provider


def read_matrix_domain(kit_root: Path) -> str:
    return read_required_domain(kit_root, "Matrix", "matrix", "domain", "matrix.example.com")


def read_matrix_element_domain(kit_root: Path, matrix_domain: str) -> str:
    """Public Element host for Kanidm's app tile (may differ from the homeserver)."""
    deploy = kit_root / "deploy.yaml"
    if not deploy.is_file():
        return ""
    element = ((load_yaml(deploy).get("features") or {}).get("element") or {})
    if not isinstance(element, dict):
        return ""
    if "enabled" in element and not to_bool(element.get("enabled")):
        return ""
    return str(element.get("domain") or matrix_domain).strip()


def _is_kanidm_sso_provider(provider: Any) -> bool:
    if not isinstance(provider, dict):
        return False
    name = str(provider.get("name") or "").strip().lower()
    client_id = str(provider.get("client_id") or "").strip().lower()
    issuer = str(provider.get("issuer") or "").strip().lower()
    if name in LOCAL_IDPS:
        return True
    if client_id == DEFAULT_MATRIX_CLIENT_ID or client_id.startswith("matrix-"):
        return True
    return "kanidm" in issuer or "/oauth2/openid/" in issuer


def matrix_should_skip(kit_root: Path, service_entry: dict) -> bool:
    if managed_is_false(service_entry.get("oidc") if isinstance(service_entry.get("oidc"), dict) else {}):
        return True
    sso = deploy_mapping(kit_root, "features", "sso")
    if managed_is_false(sso):
        return True
    provider = str(sso.get("provider") or "").strip().lower()
    if provider and provider not in LOCAL_IDPS:
        return True
    providers = sso.get("providers") if isinstance(sso.get("providers"), list) else []
    if providers and not any(_is_kanidm_sso_provider(item) for item in providers):
        if provider not in LOCAL_IDPS:
            return True
    return False


class MatrixConsumer(IdentityConsumer):
    kit = "matrix"
    label = "Matrix"
    default_client_id = DEFAULT_MATRIX_CLIENT_ID
    skip_note = "Matrix OIDC wiring skipped (features.sso.managed: false or non-Kanidm provider)."

    def should_skip(self, kit_root: Path, service: dict) -> bool:
        return matrix_should_skip(kit_root, service)

    def wire_local(self, ctx: WireContext, kit_root: Path) -> list[str]:
        cfg = ctx.consumer_cfg(self.kit)
        client_id = self.client_id(ctx)
        domain = str(cfg.get("domain") or "").strip() or read_matrix_domain(kit_root)
        matrix_sidecar = kit_root / PROVIDER_SIDECAR
        client_secret = load_existing_matrix_secret(
            matrix_sidecar, kanidm_secret_path(ctx.kanidm_root, client_id)
        )
        element_domain = str(cfg.get("element_domain") or "").strip() or read_matrix_element_domain(
            kit_root, domain
        )
        ctx.write_client(
            build_matrix_client(domain, ctx.kanidm_domain, client_id=client_id, element_domain=element_domain)
        )
        write_sidecar(
            matrix_sidecar,
            build_matrix_provider(ctx.kanidm_domain, client_id=client_id, client_secret=client_secret),
        )
        return [f"Wired Matrix ({domain}) → Kanidm ({ctx.kanidm_domain}) as client {client_id!r}."]

    def wire_remote(self, ctx: WireContext) -> list[str] | None:
        cfg = ctx.consumer_cfg(self.kit)
        domain = str(cfg.get("domain") or "").strip()
        if not domain:
            return None
        client_id = self.client_id(ctx)
        ctx.write_client(
            build_matrix_client(
                domain,
                ctx.kanidm_domain,
                client_id=client_id,
                element_domain=str(cfg.get("element_domain") or "").strip(),
            )
        )
        return [
            f"Wired remote Matrix ({domain}) client {client_id!r} into Kanidm "
            f"(split-VPS). Configure Matrix features.sso on the other host after Kanidm apply writes the secret."
        ]
