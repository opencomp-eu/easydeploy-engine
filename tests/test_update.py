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
