"""Tests for version sync and update orchestration."""

from __future__ import annotations

from pathlib import Path

import yaml

from scripts.version_sync import sync_image_tags_for_service, sync_image_tags


def _service(name: str, kit: Path) -> dict:
    return {
        "name": name,
        "path": kit,
        "fragment_rel": "x",
        "deploy": None,
    }


def test_sync_image_tags_from_example_to_kits_yaml(tmp_path: Path):
    kit = tmp_path / "opencloud-easy-deploy"
    kit.mkdir()
    (kit / "deploy.yaml.example").write_text(
        yaml.safe_dump({"opencloud": {"tag": "7.5.0", "domain": "cloud.new.example"}})
    )
    kits = tmp_path / "kits"
    kits.mkdir()
    (kits / "opencloud.yaml").write_text(
        yaml.safe_dump({"opencloud": {"tag": "7.2.0", "domain": "cloud.my.example"}})
    )

    notes = sync_image_tags_for_service(_service("opencloud", kit), tmp_path)
    assert notes == ["opencloud (opencloud.yaml): opencloud.tag: '7.2.0' -> '7.5.0'"]

    data = yaml.safe_load((kits / "opencloud.yaml").read_text())
    assert data["opencloud"]["tag"] == "7.5.0"
    assert data["opencloud"]["domain"] == "cloud.my.example"


def test_sync_image_tags_nested_stalwart_bulwark(tmp_path: Path):
    kit = tmp_path / "stalwart-easy-deploy"
    kit.mkdir()
    (kit / "deploy.yaml.example").write_text(
        yaml.safe_dump(
            {
                "stalwart": {"tag": "v0.16.20"},
                "bulwark": {"tag": "1.9.2"},
            }
        )
    )
    (kit / "deploy.yaml").write_text(
        yaml.safe_dump(
            {
                "stalwart": {"tag": "v0.16", "hostname": "mail.example"},
                "bulwark": {"tag": "1.7.5"},
            }
        )
    )

    notes = sync_image_tags_for_service(_service("stalwart", kit), tmp_path)
    assert len(notes) == 2
    data = yaml.safe_load((kit / "deploy.yaml").read_text())
    assert data["stalwart"]["tag"] == "v0.16.20"
    assert data["bulwark"]["tag"] == "1.9.2"
    assert data["stalwart"]["hostname"] == "mail.example"


def test_sync_image_tags_no_changes(tmp_path: Path):
    kit = tmp_path / "kanidm-easy-deploy"
    kit.mkdir()
    (kit / "deploy.yaml.example").write_text(
        yaml.safe_dump({"kanidm": {"tag": "1.11.1", "tools_tag": "1.11.1"}})
    )
    (kit / "deploy.yaml").write_text(
        yaml.safe_dump({"kanidm": {"tag": "1.11.1", "tools_tag": "1.11.1"}})
    )

    assert sync_image_tags_for_service(_service("kanidm", kit), tmp_path) == []


def test_sync_image_tags_enabled_services(tmp_path: Path):
    kit = tmp_path / "opencloud-easy-deploy"
    kit.mkdir()
    (kit / "deploy.yaml.example").write_text(yaml.safe_dump({"opencloud": {"tag": "7.5.0"}}))
    kits = tmp_path / "kits"
    kits.mkdir()
    (kits / "opencloud.yaml").write_text(yaml.safe_dump({"opencloud": {"tag": "7.0.0"}}))

    notes = sync_image_tags([_service("opencloud", kit)], tmp_path)
    assert len(notes) == 1
    assert "7.5.0" in notes[0]


def test_update_module_imports():
    import scripts.update as update_module

    assert callable(update_module.update_stack)
    assert callable(update_module.pull_engine_repo)


def _quiet_update_mocks(update_module, monkeypatch, tmp_path, *, apply_result):
    from scripts.progress import GitSync

    monkeypatch.setattr(update_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        update_module,
        "pull_engine_repo",
        lambda **_kwargs: GitSync(name="engine", status="already up to date", old_sha="abc", new_sha="abc"),
    )
    monkeypatch.setattr(
        update_module,
        "load_engine",
        lambda: {"engine": {"network": "easydeploy-net"}, "services": {"kanidm": {"enabled": True, "path": "/tmp/k", "fragment": "x"}}},
    )
    monkeypatch.setattr(
        update_module,
        "validate_engine",
        lambda _config: [{"name": "kanidm", "path": tmp_path / "kanidm-easy-deploy", "fragment_rel": "x"}],
    )
    monkeypatch.setattr(
        update_module,
        "sync_kit_repos",
        lambda *_args, **_kwargs: [
            GitSync(name="kanidm", status="already up to date", old_sha="def", new_sha="def")
        ],
    )
    monkeypatch.setattr(update_module, "sync_image_tags", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(update_module, "snapshot_image_ids", lambda: {"caddy:2": "id1"})
    monkeypatch.setattr(update_module, "apply_engine", lambda **_kwargs: apply_result)


def test_quiet_update_stack_prints_summary(tmp_path: Path, monkeypatch, capsys):
    import scripts.update as update_module
    from scripts.apply import ApplyResult, KitRunResult

    seen: dict = {}

    def fake_apply(**kwargs):
        seen.update(kwargs)
        return ApplyResult(kits=(KitRunResult("kanidm", "ok"),), caddy="ok")

    _quiet_update_mocks(update_module, monkeypatch, tmp_path, apply_result=None)
    monkeypatch.setattr(update_module, "apply_engine", fake_apply)

    update_module.update_stack(verbose=False)
    out = capsys.readouterr().out
    assert seen["kit_script"] == "update.sh"
    assert "--skip-git" in seen["kit_args"]
    assert "Updating Easy Deploy" in out
    assert "Git" in out
    assert "already up to date" in out
    assert "Image pins" in out
    assert "unchanged" in out
    assert "Applying" in out
    assert "kanidm" in out
    assert "caddy" in out
    assert "Docker" in out
    assert "all images already up to date" in out
    assert "Update complete." in out
    assert "Applying kit" not in out
    assert "=== Easy Deploy Engine summary ===" not in out


def test_quiet_update_nothing_to_update(tmp_path: Path, monkeypatch, capsys):
    import scripts.update as update_module
    from scripts.apply import ApplyResult, KitRunResult
    from update_lock import NOTHING_TO_UPDATE

    result = ApplyResult(
        kits=(KitRunResult("kanidm", "nothing to update"),),
        caddy="nothing to update",
    )
    _quiet_update_mocks(update_module, monkeypatch, tmp_path, apply_result=result)

    update_module.update_stack(verbose=False)
    out = capsys.readouterr().out.strip()
    assert out == NOTHING_TO_UPDATE
    assert "Updating Easy Deploy" not in out
    assert "Update complete." not in out


def test_update_stack_passes_force_and_skip_pull(tmp_path: Path, monkeypatch):
    import scripts.update as update_module
    from scripts.apply import ApplyResult, KitRunResult

    seen: dict = {}

    def fake_apply(**kwargs):
        seen.update(kwargs)
        return ApplyResult(kits=(KitRunResult("kanidm", "ok"),), caddy="ok")

    _quiet_update_mocks(update_module, monkeypatch, tmp_path, apply_result=None)
    monkeypatch.setattr(update_module, "apply_engine", fake_apply)

    update_module.update_stack(verbose=False, force=True, skip_pull=True)
    assert seen["force"] is True
    assert "--force" in seen["kit_args"]
    assert "--skip-pull" in seen["kit_args"]
    assert "--skip-git" in seen["kit_args"]


def test_quiet_update_reports_docker_image_changes(monkeypatch, capsys):
    import scripts.update as update_module
    from scripts.apply import ApplyResult, KitRunResult
    from scripts.progress import GitSync

    images = [{"caddy:2": "old"}, {"caddy:2": "new"}]

    monkeypatch.setattr(
        update_module,
        "pull_engine_repo",
        lambda **_kwargs: GitSync(name="engine", status="updated", old_sha="aaa", new_sha="bbb"),
    )
    monkeypatch.setattr(update_module, "load_engine", lambda: {})
    monkeypatch.setattr(update_module, "validate_engine", lambda _config: [])
    monkeypatch.setattr(update_module, "sync_kit_repos", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(update_module, "sync_image_tags", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(update_module, "snapshot_image_ids", lambda: images.pop(0))
    monkeypatch.setattr(
        update_module,
        "apply_engine",
        lambda **_kwargs: ApplyResult(kits=(), caddy="ok"),
    )

    update_module.update_stack(verbose=False, skip_git=True, skip_tags=True)
    out = capsys.readouterr().out
    assert "1 image(s) updated:" in out
    assert "caddy:2" in out
    assert "Update complete." in out


def _git_env() -> dict[str, str]:
    import os

    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@example.test",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@example.test",
        }
    )
    return env


def _git(repo: Path, *args: str) -> str:
    import subprocess

    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env=_git_env(),
    )
    return result.stdout.strip()


def _init_repo(repo: Path, filename: str, content: str) -> None:
    import subprocess

    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "master", str(repo)], check=True, capture_output=True)
    (repo / filename).write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")


def _branch(repo: Path, name: str, filename: str, content: str) -> None:
    _git(repo, "checkout", "-b", name)
    (repo / filename).write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", name)
    _git(repo, "checkout", "master")


def _engine_yaml(kit_branch: str = "master") -> str:
    return (
        "engine:\n"
        "  network: easydeploy-net\n"
        "  # keep this comment\n"
        f"  kit_branch: {kit_branch}\n"
        "services:\n"
        "  opencloud:\n"
        "    enabled: true\n"
        "    path: ../opencloud-easy-deploy\n"
        "    fragment: .opencloud-easy-deploy/integration/caddy.caddy\n"
    )


def _deployment(tmp_path: Path, *, kit_branch: str = "feature/auditing"):
    engine_src = tmp_path / "engine-src"
    kit_src = tmp_path / "kit-src"
    _init_repo(engine_src, "README", "master\n")
    _init_repo(kit_src, "marker", "master\n")
    _branch(engine_src, kit_branch, "README", "feature\n")
    _branch(kit_src, kit_branch, "marker", "feature\n")
    stack = tmp_path / "stack"
    engine = stack / "easydeploy-engine"
    kit = stack / "opencloud-easy-deploy"
    _git(engine_src, "clone", str(engine_src), str(engine))
    _git(kit_src, "clone", str(kit_src), str(kit))
    (engine / "engine.yaml").write_text(_engine_yaml())
    return engine, kit


def test_changes_blocking_switch_ignores_operator_yaml(tmp_path: Path):
    from scripts.update import changes_blocking_switch

    repo = tmp_path / "repo"
    _init_repo(repo, "README", "v1\n")
    (repo / "engine.yaml").write_text("engine: {}\n")
    (repo / "deploy.yaml").write_text("x: 1\n")
    (repo / "kits").mkdir()
    (repo / "kits" / "opencloud.yaml").write_text("x: 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "config")
    (repo / "engine.yaml").write_text("engine:\n  kit_branch: master\n")
    (repo / "deploy.yaml").write_text("x: 2\n")
    (repo / "kits" / "opencloud.yaml").write_text("x: 2\n")
    (repo / "README").write_text("dirty\n")
    assert changes_blocking_switch(repo) == ("README",)


def test_switch_deployment_branch_checks_out_engine_and_kit(tmp_path: Path):
    from scripts.config_edit import load_kit_branch
    from scripts.update import switch_deployment_branch

    engine, kit = _deployment(tmp_path)
    (engine / "engine.yaml").write_text(_engine_yaml() + "# operator note\n")
    switch_deployment_branch(engine, "feature/auditing")
    assert _git(engine, "branch", "--show-current") == "feature/auditing"
    assert _git(kit, "branch", "--show-current") == "feature/auditing"
    assert (engine / "README").read_text() == "feature\n"
    assert (kit / "marker").read_text() == "feature\n"
    assert load_kit_branch(engine) == "feature/auditing"
    assert "# keep this comment" in (engine / "engine.yaml").read_text()
    assert "# operator note" in (engine / "engine.yaml").read_text()


def test_switch_deployment_branch_refuses_local_edits(tmp_path: Path):
    import pytest

    from scripts.progress import UpdateFailed
    from scripts.update import switch_deployment_branch

    engine, kit = _deployment(tmp_path)
    (engine / "README").write_text("local edit\n")
    with pytest.raises(UpdateFailed, match="easydeploy-engine has local changes"):
        switch_deployment_branch(engine, "feature/auditing")
    assert _git(engine, "branch", "--show-current") == "master"
    assert _git(kit, "branch", "--show-current") == "master"


def test_switch_deployment_branch_refuses_missing_kit_branch(tmp_path: Path):
    import pytest

    from scripts.progress import UpdateFailed
    from scripts.update import switch_deployment_branch

    engine, kit = _deployment(tmp_path)
    origin = Path(_git(kit, "remote", "get-url", "origin"))
    _git(origin, "branch", "-D", "feature/auditing")
    with pytest.raises(UpdateFailed, match="has no branch feature/auditing"):
        switch_deployment_branch(engine, "feature/auditing")
    assert _git(engine, "branch", "--show-current") == "master"
    assert _git(kit, "branch", "--show-current") == "master"
    assert "kit_branch: master" in (engine / "engine.yaml").read_text()
