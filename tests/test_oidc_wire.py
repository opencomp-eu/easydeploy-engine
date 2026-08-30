"""Tests for engine identity wiring."""

from __future__ import annotations

from pathlib import Path

import yaml

from scripts.oidc_wire import (
    build_matrix_client,
    build_opencloud_client,
    build_opencloud_provider,
    build_stalwart_identity,
    identity_wire_enabled,
    kanidm_issuer_url,
    ldap_base_dn,
    matrix_kanidm_redirect_uri,
    wire_identity,
)


def _kit_kanidm(root: Path, domain: str = "idm.test.example") -> Path:
    kit = root / "kanidm-easy-deploy"
    kit.mkdir()
    (kit / "deploy.yaml").write_text(yaml.safe_dump({"kanidm": {"domain": domain}}))
    return kit


def _kit_matrix(root: Path, domain: str = "matrix.test.example", **sso) -> Path:
    kit = root / "matrix-easy-deploy"
    kit.mkdir()
    features = {"sso": {"enabled": True, "provider": "kanidm", "providers": []}}
    features["sso"].update(sso)
    (kit / "deploy.yaml").write_text(
        yaml.safe_dump({"matrix": {"domain": domain}, "features": features})
    )
    return kit


def _kit_opencloud(root: Path, domain: str = "cloud.test.example") -> Path:
    kit = root / "opencloud-easy-deploy"
    kit.mkdir()
    (kit / "deploy.yaml").write_text(yaml.safe_dump({"opencloud": {"domain": domain}}))
    return kit


def _kit_stalwart(root: Path, hostname: str = "mail.test.example") -> Path:
    kit = root / "stalwart-easy-deploy"
    kit.mkdir()
    (kit / "deploy.yaml").write_text(
        yaml.safe_dump({"stalwart": {"hostname": hostname, "domain": "test.example"}})
    )
    return kit


def test_identity_wire_false():
    assert identity_wire_enabled({"identity": {"wire": False}}) is False
    assert identity_wire_enabled({"identity": {"wire": "auto"}}) is True
    assert identity_wire_enabled({}) is True


def test_build_opencloud_client_redirects():
    client = build_opencloud_client("cloud.test.example")
    assert client["public"] is True
    assert "https://cloud.test.example/web-oidc-callback" in client["redirect_uris"]
    assert "openid" in client["scopes"]


def test_kanidm_issuer_is_per_client():
    assert kanidm_issuer_url("idm.example", "opencloud") == "https://idm.example/oauth2/openid/opencloud"
    assert ldap_base_dn("idm.example.com") == "dc=idm,dc=example,dc=com"


def test_wire_same_vps(tmp_path: Path):
    kanidm = _kit_kanidm(tmp_path)
    opencloud = _kit_opencloud(tmp_path)
    enabled = [
        {"name": "kanidm", "path": kanidm, "fragment_rel": "x", "oidc": {}},
        {"name": "opencloud", "path": opencloud, "fragment_rel": "x", "oidc": {}},
    ]
    notes = wire_identity({"identity": {"wire": "auto"}}, enabled, tmp_path)
    client_path = kanidm / ".kanidm-easy-deploy" / "integration" / "oidc-clients.d" / "opencloud.yaml"
    provider_path = opencloud / ".opencloud-easy-deploy" / "integration" / "oidc-provider.yaml"
    assert client_path.is_file()
    assert provider_path.is_file()
    client = yaml.safe_load(client_path.read_text())
    provider = yaml.safe_load(provider_path.read_text())
    assert client["client_id"] == "opencloud"
    assert provider["issuer_url"] == "https://idm.test.example/oauth2/openid/opencloud"
    assert provider["provider"] == "kanidm"
    assert any("Wired OpenCloud" in line for line in notes)


def test_wire_skips_non_kanidm_provider(tmp_path: Path):
    kanidm = _kit_kanidm(tmp_path)
    opencloud = _kit_opencloud(tmp_path)
    (opencloud / "deploy.yaml").write_text(
        yaml.safe_dump(
            {
                "opencloud": {"domain": "cloud.test.example"},
                "auth": {"mode": "oidc", "oidc": {"provider": "keycloak"}},
            }
        )
    )
    enabled = [
        {"name": "kanidm", "path": kanidm, "fragment_rel": "x", "oidc": {}},
        {"name": "opencloud", "path": opencloud, "fragment_rel": "x", "oidc": {}},
    ]
    notes = wire_identity({}, enabled, tmp_path)
    assert not (kanidm / ".kanidm-easy-deploy" / "integration" / "oidc-clients.d").exists()
    assert any("skipped" in line.lower() for line in notes)


def test_wire_remote_consumer_domain(tmp_path: Path):
    kanidm = _kit_kanidm(tmp_path)
    enabled = [{"name": "kanidm", "path": kanidm, "fragment_rel": "x", "oidc": {}}]
    notes = wire_identity(
        {
            "identity": {
                "wire": True,
                "consumers": {"opencloud": {"domain": "cloud.other.example"}},
            }
        },
        enabled,
        tmp_path,
    )
    client = yaml.safe_load(
        (kanidm / ".kanidm-easy-deploy" / "integration" / "oidc-clients.d" / "opencloud.yaml").read_text()
    )
    assert "https://cloud.other.example/" in client["redirect_uris"]
    assert any("split-VPS" in line or "remote" in line.lower() for line in notes)


def test_build_opencloud_provider_urls():
    provider = build_opencloud_provider("idm.example")
    assert provider["account_url"] == "https://idm.example/"
    assert provider["role_mapping"]["admin"] == "opencloud-admin"
    assert provider["issuer_url"].endswith("/oauth2/openid/opencloud")


def test_wire_matrix_same_vps(tmp_path: Path):
    kanidm = _kit_kanidm(tmp_path)
    matrix = _kit_matrix(tmp_path)
    enabled = [
        {"name": "kanidm", "path": kanidm, "fragment_rel": "x", "oidc": {}},
        {"name": "matrix", "path": matrix, "fragment_rel": "x", "oidc": {}},
    ]
    notes = wire_identity({"identity": {"wire": "auto"}}, enabled, tmp_path)
    client_path = kanidm / ".kanidm-easy-deploy" / "integration" / "oidc-clients.d" / "matrix.yaml"
    provider_path = matrix / ".matrix-easy-deploy" / "integration" / "oidc-provider.yaml"
    assert client_path.is_file()
    assert provider_path.is_file()
    client = yaml.safe_load(client_path.read_text())
    provider = yaml.safe_load(provider_path.read_text())
    assert client["client_id"] == "matrix"
    assert client["public"] is False
    assert provider["issuer"] == "https://idm.test.example/oauth2/openid/matrix"
    expected_redirect = matrix_kanidm_redirect_uri("matrix.test.example", "idm.test.example")
    assert client["redirect_uris"] == [expected_redirect]
    assert provider["id"] == expected_redirect.rsplit("/", 1)[-1]
    assert any("Wired Matrix" in line for line in notes)


def test_wire_skips_matrix_google_providers(tmp_path: Path):
    kanidm = _kit_kanidm(tmp_path)
    matrix = _kit_matrix(
        tmp_path,
        enabled=True,
        provider="",
        providers=[
            {
                "name": "Google",
                "issuer": "https://accounts.google.com/",
                "client_id": "g",
                "client_secret": "s",
            }
        ],
    )
    enabled = [
        {"name": "kanidm", "path": kanidm, "fragment_rel": "x", "oidc": {}},
        {"name": "matrix", "path": matrix, "fragment_rel": "x", "oidc": {}},
    ]
    notes = wire_identity({}, enabled, tmp_path)
    assert not (kanidm / ".kanidm-easy-deploy" / "integration" / "oidc-clients.d" / "matrix.yaml").exists()
    assert any("Matrix OIDC wiring skipped" in line for line in notes)


def test_wire_remote_matrix_consumer_domain(tmp_path: Path):
    kanidm = _kit_kanidm(tmp_path)
    enabled = [{"name": "kanidm", "path": kanidm, "fragment_rel": "x", "oidc": {}}]
    notes = wire_identity(
        {
            "identity": {
                "wire": True,
                "consumers": {"matrix": {"domain": "matrix.other.example"}},
            }
        },
        enabled,
        tmp_path,
    )
    client = yaml.safe_load(
        (kanidm / ".kanidm-easy-deploy" / "integration" / "oidc-clients.d" / "matrix.yaml").read_text()
    )
    assert client["redirect_uris"][0].startswith("https://matrix.other.example/auth/upstream/callback/")
    assert any("split-VPS" in line or "remote" in line.lower() for line in notes)


def test_build_matrix_client_is_confidential():
    client = build_matrix_client("matrix.example", "idm.example")
    assert client["public"] is False
    assert "openid" in client["scopes"]


def test_wire_stalwart_same_vps(tmp_path: Path):
    kanidm = _kit_kanidm(tmp_path)
    stalwart = _kit_stalwart(tmp_path)
    enabled = [
        {"name": "kanidm", "path": kanidm, "fragment_rel": "x", "oidc": {}},
        {"name": "stalwart", "path": stalwart, "fragment_rel": "x", "oidc": {}},
    ]
    notes = wire_identity({"identity": {"wire": "auto"}}, enabled, tmp_path)
    client_path = kanidm / ".kanidm-easy-deploy" / "integration" / "oidc-clients.d" / "stalwart.yaml"
    identity_path = stalwart / ".stalwart-easy-deploy" / "integration" / "identity-provider.yaml"
    assert client_path.is_file()
    assert identity_path.is_file()
    identity = yaml.safe_load(identity_path.read_text())
    assert identity["provider"] == "kanidm"
    assert identity["ldap"]["url"] == "ldaps://kanidm:3636"
    assert identity["ldap"]["base_dn"] == "dc=idm,dc=test,dc=example"
    assert identity["oidc"]["issuer_url"].endswith("/oauth2/openid/stalwart")
    assert any("Wired Stalwart" in line for line in notes)


def test_build_stalwart_identity_filters():
    identity = build_stalwart_identity("idm.example.com", "example.com")
    assert identity["ldap"]["bind_dn"] == "dn=token"
    assert identity["oidc"]["username_domain"] == "example.com"
