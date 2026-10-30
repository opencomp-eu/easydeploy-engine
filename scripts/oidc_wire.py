"""Wire Kanidm OIDC/LDAP to local (or declared) consumers without editing operator deploy.yaml.

Each consumer kit has an adapter in scripts/integrations/. Adapters write kit
integration sidecars:
- Kanidm: .kanidm-easy-deploy/integration/oidc-clients.d/<id>.yaml
- OpenCloud: .opencloud-easy-deploy/integration/oidc-provider.yaml
- Matrix: .matrix-easy-deploy/integration/oidc-provider.yaml
- Stalwart: .stalwart-easy-deploy/integration/identity-provider.yaml
- Nextcloud: .nextcloud-easy-deploy/integration/oidc-provider.yaml

Kits merge those files on apply. Operators opt out with `oidc.managed: false`
(or `auth.oidc.managed: false` / `features.sso.managed: false` / `identity.managed: false`).
"""

from __future__ import annotations

from pathlib import Path

from scripts.integrations import WireContext, identity_consumers
from scripts.integrations.common import (  # noqa: F401  (re-exported for other engine modules)
    KANIDM_SSO_NAME,
    SIDECAR_HEADER,
    consumer_should_skip,
    kanidm_issuer_url,
    kanidm_origin,
    ldap_base_dn,
    load_yaml,
    managed_is_false,
    plaintext_oidc_secret,
    read_required_domain,
    resolve_kit_path,
    to_bool,
    write_sidecar,
)
from scripts.integrations.matrix import (  # noqa: F401
    DEFAULT_MATRIX_CLIENT_ID,
    MAS_PATH_PREFIX,
    build_matrix_client,
    build_matrix_provider,
    load_existing_matrix_secret,
    matrix_kanidm_provider_id,
    matrix_kanidm_redirect_uri,
    matrix_should_skip,
    read_matrix_domain,
    read_matrix_element_domain,
    stable_provider_ulid,
)
from scripts.integrations.nextcloud import (  # noqa: F401
    build_nextcloud_client,
    build_nextcloud_provider,
    read_nextcloud_domain,
)
from scripts.integrations.opencloud import (  # noqa: F401
    DEFAULT_OIDC_SCOPES,
    OPENCLOUD_REDIRECT_PATHS,
    OPENCLOUD_ROLE_CLAIM,
    build_opencloud_client,
    build_opencloud_provider,
    read_opencloud_domain,
)
from scripts.integrations.stalwart import (  # noqa: F401
    BULWARK_CALLBACK_LOCALES,
    MAIL_USERS_GROUP,
    STALWART_LDAP_FILTER_LOGIN,
    STALWART_LDAP_FILTER_MAILBOX,
    build_stalwart_client,
    build_stalwart_identity,
    read_stalwart_domains,
    read_webmail_domain,
    stalwart_mailbox_filter,
    stalwart_should_skip,
)
from scripts.integrations.stalwart import read_ldap_token as _read_ldap_token  # noqa: F401
from scripts.stacks import resolve_stack


def identity_wire_enabled(engine_config: dict) -> bool:
    identity = engine_config.get("identity") or {}
    raw = identity.get("wire", "auto")
    if raw is False or str(raw).strip().lower() in {"false", "no", "0", "off"}:
        return False
    return True


def read_kanidm_domain(kit_root: Path) -> str:
    return read_required_domain(kit_root, "Kanidm", "kanidm", "domain", "idm.example.com")


def wire_identity(
    engine_config: dict,
    enabled: list[dict],
    project_root: Path,
) -> list[str]:
    """Write OIDC/LDAP sidecars for every consumer on the deployment path. Returns status lines."""
    if not identity_wire_enabled(engine_config):
        return ["Identity wiring skipped (identity.wire: false)."]

    by_name = {item["name"]: item for item in enabled}
    identity = engine_config.get("identity") or {}
    consumers_cfg = identity.get("consumers") or {}
    if not isinstance(consumers_cfg, dict):
        consumers_cfg = {}

    kanidm = by_name.get("kanidm")
    if not kanidm:
        return ["Identity wiring skipped (Kanidm is not enabled in engine.yaml)."]

    kanidm_root = resolve_kit_path(kanidm, project_root)
    ctx = WireContext(
        engine_config=engine_config,
        enabled=by_name,
        project_root=project_root,
        kanidm_root=kanidm_root,
        kanidm_domain=str(identity.get("domain") or "").strip() or read_kanidm_domain(kanidm_root),
        consumers=consumers_cfg,
    )
    consumers = identity_consumers(resolve_stack(engine_config, by_name))

    notes: list[str] = []
    wired = 0
    for consumer in consumers:
        did_wire, lines = consumer.wire(ctx)
        notes.extend(lines)
        wired += int(did_wire)

    if wired == 0 and not notes:
        labels = "/".join(consumer.label for consumer in consumers)
        notes.append(
            f"Identity wiring: no consumers enabled ({labels}) and no identity.consumers.domain set."
        )
    if wired:
        notes.append(
            "Re-run apply.sh in kanidm-easy-deploy, then each app kit, so they pick up the sidecars. "
            "On a same-VPS install, bash wizard.sh in easydeploy-engine handles this order."
        )
    return notes
