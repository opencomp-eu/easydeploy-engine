"""Tests for engine OIDC wiring."""

from __future__ import annotations

from pathlib import Path

import yaml

from scripts.oidc_wire import (
    build_opencloud_client,
    build_opencloud_provider,
    identity_wire_enabled,
    wire_identity,
)


def _kit_authelia(root: Path, domain: str = "auth.test.example") -> Path:
    kit = root / "authelia-easy-deploy"
    kit.mkdir()
    (kit / "deploy.yaml").write_text(
        yaml.safe_dump({"authelia": {"domain": domain, "sso_domain": "test.example"}})
    )
    return kit


def _kit_opencloud(root: Path, domain: str = "cloud.test.example") -> Path:
    kit = root / "opencloud-easy-deploy"
    kit.mkdir()
    (kit / "deploy.yaml").write_text(yaml.safe_dump({"opencloud": {"domain": domain}}))
    return kit


def test_identity_wire_false():
    assert identity_wire_enabled({"identity": {"wire": False}}) is False
    assert identity_wire_enabled({"identity": {"wire": "auto"}}) is True
    assert identity_wire_enabled({}) is True


def test_build_opencloud_client_redirects():
    client = build_opencloud_client("cloud.test.example")
    assert client["public"] is True
    assert client["token_endpoint_auth_method"] == "none"
    assert "https://cloud.test.example/web-oidc-callback" in client["redirect_uris"]
    assert "offline_access" in client["scopes"]


def test_wire_same_vps(tmp_path: Path):
    authelia = _kit_authelia(tmp_path)
    opencloud = _kit_opencloud(tmp_path)
    enabled = [
        {"name": "authelia", "path": authelia, "fragment_rel": "x", "oidc": {}},
        {"name": "opencloud", "path": opencloud, "fragment_rel": "x", "oidc": {}},
    ]
    notes = wire_identity({"identity": {"wire": "auto"}}, enabled, tmp_path)
    client_path = authelia / ".authelia-easy-deploy" / "integration" / "oidc-clients.d" / "opencloud.yaml"
    provider_path = opencloud / ".opencloud-easy-deploy" / "integration" / "oidc-provider.yaml"
    assert client_path.is_file()
    assert provider_path.is_file()
    client = yaml.safe_load(client_path.read_text())
    provider = yaml.safe_load(provider_path.read_text())
    assert client["client_id"] == "opencloud"
    assert provider["issuer_url"] == "https://auth.test.example"
    assert any("Wired OpenCloud" in line for line in notes)


def test_wire_skips_non_authelia_provider(tmp_path: Path):
    authelia = _kit_authelia(tmp_path)
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
        {"name": "authelia", "path": authelia, "fragment_rel": "x", "oidc": {}},
        {"name": "opencloud", "path": opencloud, "fragment_rel": "x", "oidc": {}},
    ]
    notes = wire_identity({}, enabled, tmp_path)
    assert not (authelia / ".authelia-easy-deploy" / "integration" / "oidc-clients.d").exists()
    assert any("skipped" in line.lower() for line in notes)


def test_wire_remote_consumer_domain(tmp_path: Path):
    authelia = _kit_authelia(tmp_path)
    enabled = [{"name": "authelia", "path": authelia, "fragment_rel": "x", "oidc": {}}]
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
        (
            authelia / ".authelia-easy-deploy" / "integration" / "oidc-clients.d" / "opencloud.yaml"
        ).read_text()
    )
    assert "https://cloud.other.example/" in client["redirect_uris"]
    assert any("split-VPS" in line or "remote" in line.lower() for line in notes)


def test_build_opencloud_provider_urls():
    provider = build_opencloud_provider("auth.example")
    assert provider["account_url"] == "https://auth.example/"
    assert provider["role_mapping"]["admin"] == "opencloud-admin"
