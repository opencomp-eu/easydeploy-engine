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
    kit = tmp_path / "kanidm"
    frag = kit / ".kanidm-easy-deploy" / "integration"
    frag.mkdir(parents=True)
    (frag / "caddy.caddy").write_text("idm.test.example {\n    reverse_proxy https://kanidm:8443\n}\n")

    enabled = [
        {
            "name": "kanidm",
            "path": kit,
            "fragment_rel": ".kanidm-easy-deploy/integration/caddy.caddy",
        }
    ]
    fragments = collect_fragments(enabled)
    assert len(fragments) == 1
    caddy = render_caddyfile(fragments)
    assert "idm.test.example" in caddy
    assert "kanidm-easy-deploy" in caddy


def test_resolve_operator_deploy_uses_kits_dir(tmp_path: Path):
    kits = tmp_path / "kits"
    kits.mkdir()
    (kits / "kanidm.yaml").write_text("kanidm: {}\n")
    assert resolve_operator_deploy("kanidm", None, tmp_path) == (kits / "kanidm.yaml").resolve()
    assert resolve_operator_deploy("opencloud", None, tmp_path) is None
    assert resolve_operator_deploy("kanidm", False, tmp_path) is None
    assert resolve_operator_deploy("kanidm", "custom.yaml", tmp_path) == (tmp_path / "custom.yaml").resolve()


def test_seed_kit_deploy_copies_and_sets_integrate(tmp_path: Path):
    import yaml

    kit = tmp_path / "kanidm-easy-deploy"
    kit.mkdir()
    seed = tmp_path / "kits" / "kanidm.yaml"
    seed.parent.mkdir()
    seed.write_text("kanidm:\n  domain: idm.example.com\nproxy:\n  type: caddy\n  mode: standalone\n")
    service = {
        "name": "kanidm",
        "path": kit,
        "fragment_rel": ".kanidm-easy-deploy/integration/caddy.caddy",
        "deploy": None,
    }
    dest = seed_kit_deploy(service, tmp_path)
    data = yaml.safe_load(dest.read_text())
    assert dest == kit / "deploy.yaml"
    assert data["kanidm"]["domain"] == "idm.example.com"
    assert data["proxy"]["mode"] == "integrate"


def _seed_scheduled_kit(tmp_path: Path) -> dict:
    kit = tmp_path / "kanidm-easy-deploy"
    kit.mkdir()
    seed = tmp_path / "kits" / "kanidm.yaml"
    seed.parent.mkdir()
    seed.write_text(
        "kanidm:\n  domain: idm.example.com\n"
        "backup:\n  enabled: true\n  schedule:\n    enabled: true\n    calendar: '*-*-* 03:00:00'\n"
    )
    return {"name": "kanidm", "path": kit, "fragment_rel": "x", "deploy": None}


def test_seed_kit_deploy_disables_kit_timer_when_engine_schedules(tmp_path: Path):
    import yaml

    dest = seed_kit_deploy(_seed_scheduled_kit(tmp_path), tmp_path, engine_schedules=True)
    backup = yaml.safe_load(dest.read_text())["backup"]
    assert backup["enabled"] is True
    assert backup["schedule"] == {"enabled": False, "calendar": "*-*-* 03:00:00"}


def test_seed_kit_deploy_keeps_kit_timer_without_engine_schedule(tmp_path: Path):
    import yaml

    dest = seed_kit_deploy(_seed_scheduled_kit(tmp_path), tmp_path)
    assert yaml.safe_load(dest.read_text())["backup"]["schedule"]["enabled"] is True


@pytest.mark.parametrize(
    ("backup", "expected"),
    [
        ({"enabled": True, "schedule": {"enabled": True}}, True),
        ({"enabled": True, "schedule": {"enabled": False}}, False),
        ({"enabled": False, "schedule": {"enabled": True}}, False),
        ({"enabled": True}, False),
        (None, False),
    ],
)
def test_engine_schedules_backups(backup, expected):
    from scripts.apply import engine_schedules_backups

    assert engine_schedules_backups({"backup": backup}) is expected


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


def test_run_kit_applies_drops_parent_venv(tmp_path, monkeypatch):
    import subprocess

    import scripts.apply as engine_apply

    kit = tmp_path / "kanidm-easy-deploy"
    kit.mkdir()
    (kit / "apply.sh").write_text("#!/bin/bash\n")
    captured: dict = {}

    def fake_run(*args, **kwargs):
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(args[0], 0)

    monkeypatch.setenv("VIRTUAL_ENV", "/opt/engine/.venv")
    monkeypatch.setenv("UV_PROJECT", "/opt/engine")
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(engine_apply, "PROJECT_ROOT", tmp_path)

    engine_apply.run_kit_applies(
        [{"name": "kanidm", "path": str(kit)}]
    )
    env = captured["env"]
    assert env is not None
    assert "VIRTUAL_ENV" not in env
    assert "UV_PROJECT" not in env
    assert env.get("EASYDEPLOY_VERBOSE") == "1"


def test_run_kit_applies_quiet_failure_writes_log(tmp_path, monkeypatch, capsys):
    import subprocess

    import pytest

    import scripts.apply as engine_apply
    from scripts.progress import UpdateFailed

    kit = tmp_path / "opencloud-easy-deploy"
    kit.mkdir()
    (kit / "apply.sh").write_text("#!/bin/bash\n")
    state = tmp_path / ".easydeploy-engine"

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args[0], 1, stdout="pulling images\n", stderr="euro-office failed\n"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(engine_apply, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(engine_apply, "STATE_DIR", state)

    with pytest.raises(UpdateFailed, match="opencloud failed to apply") as raised:
        engine_apply.run_kit_applies(
            [{"name": "opencloud", "path": str(kit)}],
            verbose=False,
        )
    out = capsys.readouterr().out
    assert "failed" in out
    log_path = state / "logs" / "update-opencloud.log"
    assert log_path.is_file()
    assert "euro-office failed" in log_path.read_text()
    assert "bash update.sh --verbose" in str(raised.value)


def test_run_kit_scripts_uses_update_sh_and_detects_noop(tmp_path, monkeypatch):
    import subprocess

    import scripts.apply as engine_apply

    kit = tmp_path / "kanidm-easy-deploy"
    kit.mkdir()
    (kit / "update.sh").write_text("#!/bin/bash\n")
    captured: dict = {}

    def fake_run(*args, **kwargs):
        captured["cmd"] = list(args[0])
        return subprocess.CompletedProcess(args[0], 0, stdout="Nothing to update\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(engine_apply, "PROJECT_ROOT", tmp_path)

    results = engine_apply.run_kit_scripts(
        [{"name": "kanidm", "path": str(kit)}],
        script_name="update.sh",
        extra_args=["--skip-git"],
        verbose=False,
    )
    assert captured["cmd"][:3] == ["bash", str(kit / "update.sh"), "--skip-git"]
    assert results[0].status == "nothing to update"


def test_apply_result_all_noop():
    from scripts.apply import ApplyResult, KitRunResult

    noop = ApplyResult(
        kits=(KitRunResult("kanidm", "nothing to update"),),
        caddy="nothing to update",
    )
    assert noop.all_noop
    mixed = ApplyResult(kits=(KitRunResult("kanidm", "ok"),), caddy="nothing to update")
    assert not mixed.all_noop


def test_reload_stalwart_identity_runs_only_when_pending(tmp_path, monkeypatch):
    import subprocess

    from scripts import apply as engine_apply

    kit = tmp_path / "stalwart-easy-deploy"
    kit.mkdir()
    (kit / "apply.sh").write_text("#!/bin/bash\n")
    pending = kit / ".stalwart-easy-deploy" / "oidc-reload.pending"
    captured: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        captured.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(engine_apply.subprocess, "run", fake_run)
    service = {"name": "stalwart", "path": str(kit)}
    engine_apply.reload_stalwart_identity([service], verbose=False)
    assert captured == []
    pending.parent.mkdir(parents=True)
    pending.write_text("pending\n")
    engine_apply.reload_stalwart_identity([service], verbose=False)
    assert captured == [["bash", str(kit / "apply.sh"), "--reload-identity"]]
    engine_apply.reload_stalwart_identity([{"name": "kanidm", "path": str(kit)}], verbose=False)
    assert len(captured) == 1


def test_reload_stalwart_identity_failure_warns_without_failing(tmp_path, monkeypatch, capsys):
    import subprocess

    from scripts import apply as engine_apply

    kit = tmp_path / "stalwart-easy-deploy"
    pending = kit / ".stalwart-easy-deploy" / "oidc-reload.pending"
    pending.parent.mkdir(parents=True)
    pending.write_text("pending\n")
    (kit / "apply.sh").write_text("#!/bin/bash\n")

    def fake_run(cmd, **_kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Discovery fetch failed\n")

    monkeypatch.setattr(engine_apply.subprocess, "run", fake_run)
    engine_apply.reload_stalwart_identity([{"name": "stalwart", "path": str(kit)}], verbose=False)
    err = capsys.readouterr().err
    assert "webmail SSO will fail" in err
    assert "Discovery fetch failed" in err


def test_engine_update_spec_lock_path():
    from scripts.apply import STATE_DIR, engine_update_spec

    spec = engine_update_spec()
    assert spec.lock_path == STATE_DIR / "update.lock"
    assert spec.compose_projects == ("easydeploy-engine",)
