"""Tests for engine embed wiring (Bulwark iframe parents)."""

from __future__ import annotations

from pathlib import Path

import yaml

from scripts.embed_wire import (
    collect_embed_parents,
    https_origin,
    unique_https_origins,
    wire_embed,
)


def _kit_opencloud(root: Path, domain: str = "cloud.test.example") -> Path:
    kit = root / "opencloud-easy-deploy"
    kit.mkdir()
    (kit / "deploy.yaml").write_text(yaml.safe_dump({"opencloud": {"domain": domain}}))
    return kit


def _kit_matrix(root: Path, domain: str = "matrix.test.example", element: str = "") -> Path:
    kit = root / "matrix-easy-deploy"
    kit.mkdir()
    data = {"matrix": {"domain": domain}, "features": {"element": {"enabled": True}}}
    if element:
        data["features"]["element"]["domain"] = element
    (kit / "deploy.yaml").write_text(yaml.safe_dump(data))
    return kit


def _kit_stalwart(root: Path, webmail: str = "webmail.test.example", enabled: bool = True) -> Path:
    kit = root / "stalwart-easy-deploy"
    kit.mkdir()
    (kit / "deploy.yaml").write_text(
        yaml.safe_dump(
            {
                "stalwart": {"hostname": "mail.test.example", "domain": "test.example"},
                "bulwark": {"enabled": enabled, "domain": webmail},
            }
        )
    )
    return kit


def test_https_origin_normalizes_host_and_url():
    assert https_origin("webmail.test.example") == "https://webmail.test.example"
    assert https_origin("https://Webmail.test.example/path") == "https://webmail.test.example"
    assert https_origin("webmail.example.com") == ""
    assert unique_https_origins(["webmail.test.example", "https://webmail.test.example/"]) == [
        "https://webmail.test.example"
    ]


def test_wire_embed_uses_bulwark_domain(tmp_path: Path):
    opencloud = _kit_opencloud(tmp_path)
    matrix = _kit_matrix(tmp_path, element="chat.test.example")
    stalwart = _kit_stalwart(tmp_path, webmail="mailui.test.example")
    enabled = [
        {"name": "opencloud", "path": opencloud, "fragment_rel": "x", "oidc": {}},
        {"name": "matrix", "path": matrix, "fragment_rel": "x", "oidc": {}},
        {"name": "stalwart", "path": stalwart, "fragment_rel": "x", "oidc": {}},
    ]
    notes = wire_embed({}, enabled, tmp_path)
    oc_sidecar = opencloud / ".opencloud-easy-deploy" / "integration" / "embed.yaml"
    mx_sidecar = matrix / ".matrix-easy-deploy" / "integration" / "embed.yaml"
    assert yaml.safe_load(oc_sidecar.read_text())["frame_ancestors"] == [
        "https://mailui.test.example"
    ]
    assert yaml.safe_load(mx_sidecar.read_text())["frame_ancestors"] == [
        "https://mailui.test.example"
    ]
    assert any("mailui.test.example" in line for line in notes)


def test_wire_embed_skips_when_bulwark_disabled(tmp_path: Path):
    opencloud = _kit_opencloud(tmp_path)
    stalwart = _kit_stalwart(tmp_path, enabled=False)
    enabled = [
        {"name": "opencloud", "path": opencloud, "fragment_rel": "x", "oidc": {}},
        {"name": "stalwart", "path": stalwart, "fragment_rel": "x", "oidc": {}},
    ]
    sidecar = opencloud / ".opencloud-easy-deploy" / "integration" / "embed.yaml"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text("frame_ancestors: [https://stale.example]\n")
    wire_embed({}, enabled, tmp_path)
    assert not sidecar.exists()


def test_collect_embed_parents_uses_engine_extras_and_split_vps(tmp_path: Path):
    enabled = [{"name": "opencloud", "path": tmp_path / "missing", "fragment_rel": "x"}]
    origins = collect_embed_parents(
        {
            "embed": {"frame_ancestors": ["portal.test.example"]},
            "identity": {"consumers": {"stalwart": {"webmail": "webmail.other.example"}}},
        },
        enabled,
        tmp_path,
    )
    assert origins == [
        "https://portal.test.example",
        "https://webmail.other.example",
    ]
