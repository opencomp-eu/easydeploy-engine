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


def test_quiet_update_stack_prints_summary(tmp_path: Path, monkeypatch, capsys):
    import scripts.update as update_module
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
    monkeypatch.setattr(update_module, "apply_engine", lambda **_kwargs: None)

    update_module.update_stack(verbose=False)
    out = capsys.readouterr().out
    assert "Updating Easy Deploy" in out
    assert "Git" in out
    assert "already up to date" in out
    assert "Image pins" in out
    assert "unchanged" in out
    assert "Docker" in out
    assert "all images already up to date" in out
    assert "Update complete." in out
    assert "Applying kit" not in out
    assert "=== Easy Deploy Engine summary ===" not in out


def test_quiet_update_reports_docker_image_changes(monkeypatch, capsys):
    import scripts.update as update_module
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
    monkeypatch.setattr(update_module, "apply_engine", lambda **_kwargs: None)

    update_module.update_stack(verbose=False, skip_git=True, skip_tags=True)
    out = capsys.readouterr().out
    assert "1 image(s) updated:" in out
    assert "caddy:2" in out
    assert "Update complete." in out
