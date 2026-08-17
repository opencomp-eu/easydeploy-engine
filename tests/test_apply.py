"""Tests for easydeploy-engine."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.apply import (
    collect_fragments,
    render_caddyfile,
    resolve_operator_deploy,
    seed_kit_deploy,
    validate_engine,
)


def test_validate_engine_requires_enabled_service(tmp_path: Path):
    config = {"engine": {"network": "easydeploy-net"}, "services": {}}
    with pytest.raises(ValueError, match="Enable at least one"):
        validate_engine(config)


def test_compose_project_name_is_unique():
    from scripts.apply import COMPOSE_PROJECT_NAME

    assert COMPOSE_PROJECT_NAME == "easydeploy-engine"
    assert COMPOSE_PROJECT_NAME != "compose"


def test_collect_and_render_fragments(tmp_path: Path):
    kit = tmp_path / "authelia"
    frag = kit / ".authelia-easy-deploy" / "integration"
    frag.mkdir(parents=True)
    (frag / "caddy.caddy").write_text("auth.test.example {\n    reverse_proxy authelia:9091\n}\n")

    enabled = [
        {
            "name": "authelia",
            "path": kit,
            "fragment_rel": ".authelia-easy-deploy/integration/caddy.caddy",
        }
    ]
    fragments = collect_fragments(enabled)
    assert len(fragments) == 1
    caddy = render_caddyfile(fragments)
    assert "auth.test.example" in caddy
    assert "authelia-easy-deploy" in caddy


def test_resolve_operator_deploy_uses_kits_dir(tmp_path: Path):
    kits = tmp_path / "kits"
    kits.mkdir()
    (kits / "authelia.yaml").write_text("authelia: {}\n")
    assert resolve_operator_deploy("authelia", None, tmp_path) == (kits / "authelia.yaml").resolve()
    assert resolve_operator_deploy("opencloud", None, tmp_path) is None
    assert resolve_operator_deploy("authelia", False, tmp_path) is None
    assert resolve_operator_deploy("authelia", "custom.yaml", tmp_path) == (tmp_path / "custom.yaml").resolve()


def test_seed_kit_deploy_copies_and_sets_integrate(tmp_path: Path):
    import yaml

    kit = tmp_path / "authelia-easy-deploy"
    kit.mkdir()
    seed = tmp_path / "kits" / "authelia.yaml"
    seed.parent.mkdir()
    seed.write_text("authelia:\n  domain: auth.example.com\nproxy:\n  type: caddy\n  mode: standalone\n")
    service = {
        "name": "authelia",
        "path": kit,
        "fragment_rel": ".authelia-easy-deploy/integration/caddy.caddy",
        "deploy": None,
    }
    dest = seed_kit_deploy(service, tmp_path)
    data = yaml.safe_load(dest.read_text())
    assert dest == kit / "deploy.yaml"
    assert data["authelia"]["domain"] == "auth.example.com"
    assert data["proxy"]["mode"] == "integrate"


def test_seed_kit_deploy_missing_yaml(tmp_path: Path):
    kit = tmp_path / "opencloud-easy-deploy"
    kit.mkdir()
    service = {"name": "opencloud", "path": kit, "fragment_rel": "x", "deploy": None}
    with pytest.raises(FileNotFoundError, match="kits/opencloud.yaml"):
        seed_kit_deploy(service, tmp_path)


def test_collect_caddy_overlays_from_fragment_dir(tmp_path: Path, monkeypatch):
    import scripts.apply as engine_apply

    monkeypatch.setattr(engine_apply, "STATE_DIR", tmp_path / ".easydeploy-engine")

    kit = tmp_path / "matrix-easy-deploy"
    integ = kit / ".matrix-easy-deploy" / "integration"
    integ.mkdir(parents=True)
    (integ / "caddy.caddy").write_text("matrix.example.com {\n    reverse_proxy matrix_synapse:8008\n}\n")
    (integ / "engine-caddy.yml").write_text(
        "services:\n  caddy:\n    extra_hosts:\n      - host.docker.internal:host-gateway\n"
    )
    enabled = [
        {
            "name": "matrix",
            "path": kit,
            "fragment_rel": ".matrix-easy-deploy/integration/caddy.caddy",
        }
    ]
    overlays = engine_apply.collect_caddy_overlays(enabled)
    assert overlays == [integ / "engine-caddy.yml"]

    dest = engine_apply.assemble_caddy_runtime_overlay(enabled)
    assert dest is not None
    assert dest.is_file()
    assert "host.docker.internal" in dest.read_text()
