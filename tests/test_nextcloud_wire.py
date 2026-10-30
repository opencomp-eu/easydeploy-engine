from __future__ import annotations

from pathlib import Path

import yaml

from scripts.mail_wire import wire_mail
from scripts.oidc_wire import build_nextcloud_client, wire_identity

CLIENTS = Path(".kanidm-easy-deploy/integration/oidc-clients.d")
NC_INTEGRATION = Path(".nextcloud-easy-deploy/integration")


def _kit(root: Path, name: str, data: dict) -> Path:
    kit = root / f"{name}-easy-deploy"
    kit.mkdir()
    (kit / "deploy.yaml").write_text(yaml.safe_dump(data))
    return kit


def _nextcloud_path(tmp_path: Path, *, nextcloud: dict | None = None, stalwart: bool = True) -> list[dict]:
    kanidm = _kit(tmp_path, "kanidm", {"kanidm": {"domain": "idm.test.example"}})
    cloud = _kit(tmp_path, "nextcloud", nextcloud or {"nextcloud": {"domain": "cloud.test.example"}})
    enabled = [
        {"name": "kanidm", "path": kanidm, "fragment_rel": "x", "oidc": {}},
        {"name": "nextcloud", "path": cloud, "fragment_rel": "x", "oidc": {}},
    ]
    if stalwart:
        mail = _kit(
            tmp_path,
            "stalwart",
            {
                "stalwart": {"hostname": "mail.test.example", "domain": "test.example"},
                "bulwark": {"enabled": False},
                "ports": {"imaps": 993, "submissions": 465, "sieve": 4190},
            },
        )
        enabled.insert(1, {"name": "stalwart", "path": mail, "fragment_rel": "x", "oidc": {}})
    return enabled


def test_nextcloud_client_is_confidential_with_admin_group():
    client = build_nextcloud_client("cloud.test.example")
    assert client["public"] is False
    assert client["redirect_uris"] == [
        "https://cloud.test.example/apps/user_oidc/code",
        "https://cloud.test.example/index.php/apps/user_oidc/code",
    ]
    assert client["scopes"] == ["openid", "profile", "email"]
    assert client["groups"] == ["nextcloud-admins"]
    assert client["operator_groups"] == ["nextcloud-admins"]
    mapping = client["claim_maps"][0]
    assert mapping["claim"] == "nextcloud_groups"
    assert mapping["mappings"] == [{"group": "nextcloud-admins", "values": ["admin"]}]


def test_wire_nextcloud_path(tmp_path: Path):
    enabled = _nextcloud_path(tmp_path)
    notes = wire_identity({}, enabled, tmp_path)
    kanidm = enabled[0]["path"]
    cloud = enabled[2]["path"]

    clients = sorted(path.name for path in (kanidm / CLIENTS).iterdir())
    assert clients == ["nextcloud.yaml", "stalwart-webui.yaml"]
    stalwart_client = yaml.safe_load((kanidm / CLIENTS / "stalwart-webui.yaml").read_text())
    assert stalwart_client["client_name"] == "Stalwart"

    provider = yaml.safe_load((cloud / NC_INTEGRATION / "oidc-provider.yaml").read_text())
    assert provider["discovery_url"] == (
        "https://idm.test.example/oauth2/openid/nextcloud/.well-known/openid-configuration"
    )
    assert provider["client_secret"] == {
        "file": str(kanidm / ".kanidm-easy-deploy/integration/oidc-secrets.d/nextcloud.yaml"),
        "key": "client_secret",
    }
    assert provider["mapping"]["uid"] == "preferred_username"
    assert provider["mapping"]["groups"] == "nextcloud_groups"
    assert any("Wired Nextcloud" in line for line in notes)


def test_nextcloud_opt_out(tmp_path: Path):
    enabled = _nextcloud_path(
        tmp_path,
        nextcloud={"nextcloud": {"domain": "cloud.test.example"}, "auth": {"oidc": {"managed": False}}},
    )
    notes = wire_identity({}, enabled, tmp_path)
    assert not (enabled[2]["path"] / NC_INTEGRATION / "oidc-provider.yaml").exists()
    assert any("Nextcloud OIDC wiring skipped" in line for line in notes)


def test_wire_mail_points_nextcloud_at_stalwart(tmp_path: Path):
    enabled = _nextcloud_path(tmp_path)
    notes = wire_mail({}, enabled, tmp_path)
    stalwart = enabled[1]["path"]
    mail = yaml.safe_load((enabled[2]["path"] / NC_INTEGRATION / "mail.yaml").read_text())
    assert mail["email_domain"] == "test.example"
    assert mail["imap"] == {"host": "mail.test.example", "port": 993, "ssl": "ssl"}
    assert mail["smtp"] == {"host": "mail.test.example", "port": 465, "ssl": "ssl"}
    assert mail["sieve"]["port"] == 4190
    assert mail["master_user"] == "admin"
    assert mail["master_user_separator"] == "%"
    assert mail["master_password"] == {
        "file": str(stalwart / ".stalwart-easy-deploy/secrets.yaml"),
        "key": "RECOVERY_ADMIN_PASSWORD",
    }
    assert any("Nextcloud Mail" in line for line in notes)


def test_wire_mail_removes_sidecar_without_stalwart(tmp_path: Path):
    enabled = _nextcloud_path(tmp_path, stalwart=False)
    sidecar = enabled[1]["path"] / NC_INTEGRATION / "mail.yaml"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text("provider: stalwart\n")
    notes = wire_mail({}, enabled, tmp_path)
    assert not sidecar.exists()
    assert any("no mailbox is provisioned" in line for line in notes)


def test_wire_mail_is_noop_on_opencloud_matrix(tmp_path: Path):
    kanidm = _kit(tmp_path, "kanidm", {"kanidm": {"domain": "idm.test.example"}})
    enabled = [{"name": "kanidm", "path": kanidm, "fragment_rel": "x", "oidc": {}}]
    assert wire_mail({}, enabled, tmp_path) == []
