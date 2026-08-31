"""Merge image version fields from kit deploy.yaml.example into operator YAML."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from scripts.apply import resolve_operator_deploy
from scripts.oidc_wire import resolve_kit_path

VERSION_KEYS = frozenset({"tag", "tools_tag"})


def _extract_version_fields(node: object) -> dict[str, Any]:
    if not isinstance(node, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in node.items():
        if isinstance(value, dict):
            nested = _extract_version_fields(value)
            if nested:
                out[str(key)] = nested
        elif key in VERSION_KEYS:
            out[str(key)] = value
    return out


def _merge_version_fields(
    dest: dict[str, Any],
    source: dict[str, Any],
    *,
    prefix: str = "",
) -> list[str]:
    changes: list[str] = []
    for key, value in source.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            current = dest.get(key)
            if not isinstance(current, dict):
                current = {}
                dest[key] = current
            changes.extend(_merge_version_fields(current, value, prefix=path))
            continue
        old = dest.get(key)
        if old == value:
            continue
        dest[key] = value
        changes.append(f"{path}: {old!r} -> {value!r}")
    return changes


def sync_image_tags_for_service(
    service: dict,
    project_root: Path,
) -> list[str]:
    """Copy tag/tools_tag from kit deploy.yaml.example into operator deploy YAML."""
    kit_root = resolve_kit_path(service, project_root)
    example_path = kit_root / "deploy.yaml.example"
    if not example_path.is_file():
        return []

    operator_path = resolve_operator_deploy(
        service["name"],
        service.get("deploy"),
        project_root,
    )
    if operator_path is None:
        operator_path = kit_root / "deploy.yaml"
    if not operator_path.is_file():
        return []

    example = yaml.safe_load(example_path.read_text()) or {}
    versions = _extract_version_fields(example)
    if not versions:
        return []

    operator = yaml.safe_load(operator_path.read_text()) or {}
    if not isinstance(operator, dict):
        raise ValueError(f"{operator_path}: root must be a mapping")

    changes = _merge_version_fields(operator, versions)
    if not changes:
        return []

    operator_path.parent.mkdir(parents=True, exist_ok=True)
    with operator_path.open("w") as handle:
        yaml.safe_dump(operator, handle, default_flow_style=False, sort_keys=False)

    return [f"{service['name']} ({operator_path.name}): {change}" for change in changes]


def sync_image_tags(
    enabled: list[dict],
    project_root: Path,
) -> list[str]:
    notes: list[str] = []
    for service in enabled:
        for line in sync_image_tags_for_service(service, project_root):
            notes.append(line)
    return notes
