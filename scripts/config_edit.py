#!/usr/bin/env python3
"""Read and write engine.yaml for wizard and CLI tooling."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENGINE_PATH = PROJECT_ROOT / "engine.yaml"

# Kits the engine can discover. orchestrate=True means the wizard may clone the
# repo as a sibling and run wizard.sh. Kits stay standalone-deployable on their own.
KIT_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "name": "authelia",
        "dirname": "authelia-easy-deploy",
        "repo": "https://github.com/opencomp-eu/authelia-easy-deploy.git",
        "fragment": ".authelia-easy-deploy/integration/caddy.caddy",
        "label": "Authelia",
        "orchestrate": True,
    },
    {
        "name": "opencloud",
        "dirname": "opencloud-easy-deploy",
        "repo": "https://github.com/opencomp-eu/opencloud-easy-deploy.git",
        "fragment": ".opencloud-easy-deploy/integration/caddy.caddy",
        "label": "OpenCloud",
        "orchestrate": True,
    },
    {
        "name": "matrix",
        "dirname": "matrix-easy-deploy",
        "repo": "https://github.com/opencomp-eu/matrix-easy-deploy.git",
        "fragment": ".matrix-easy-deploy/integration/caddy.caddy",
        "label": "Matrix",
        "orchestrate": False,
    },
)

WIZARD_NAME = "wizard.sh"
APPLY_NAME = "apply.sh"
DEPLOY_NAME = "deploy.yaml"


def catalog_by_name(name: str) -> dict[str, Any]:
    for kit in KIT_CATALOG:
        if kit["name"] == name:
            return kit
    raise KeyError(f"unknown kit {name!r}")


def kit_dest(engine_root: Path, dirname: str) -> Path:
    return (engine_root.parent / dirname).resolve()


def load_yaml(path: Path) -> dict:
    with path.open() as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: root must be a mapping")
    return data


def save_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        yaml.safe_dump(data, handle, default_flow_style=False, sort_keys=False)


def load_or_init(path: Path, example: Path | None = None) -> dict:
    if path.exists():
        return load_yaml(path)
    example_path = example if example is not None else PROJECT_ROOT / "engine.yaml.example"
    if example_path.is_file():
        return load_yaml(example_path)
    return {}


def kit_is_present(kit_path: Path) -> bool:
    return (kit_path / APPLY_NAME).is_file() or (kit_path / WIZARD_NAME).is_file()


def describe_kit(kit: dict[str, Any], engine_root: Path) -> dict[str, Any]:
    """Layout for one catalog entry, whether or not it is cloned yet."""
    kit_path = kit_dest(engine_root, kit["dirname"])
    present = kit_is_present(kit_path)
    return {
        "name": kit["name"],
        "label": kit["label"],
        "dirname": kit["dirname"],
        "fragment": kit["fragment"],
        "repo": kit["repo"],
        "orchestrate": bool(kit.get("orchestrate")),
        "path": kit_path,
        "rel_path": os.path.relpath(kit_path, engine_root),
        "found": present,
        "has_deploy": (kit_path / DEPLOY_NAME).is_file(),
        "has_wizard": (kit_path / WIZARD_NAME).is_file(),
    }


def describe_catalog(engine_root: Path = PROJECT_ROOT) -> list[dict[str, Any]]:
    return [describe_kit(kit, engine_root) for kit in KIT_CATALOG]


def discover_kits(engine_root: Path = PROJECT_ROOT) -> list[dict[str, Any]]:
    """Sibling kit checkouts that already exist next to the engine repo."""
    return [item for item in describe_catalog(engine_root) if item["found"]]


def emit_wizard_discover(engine_root: Path = PROJECT_ROOT) -> str:
    """Bash-eval-safe KEY=value lines for wizard.sh (includes uncloned kits)."""
    lines: list[str] = []
    for item in describe_catalog(engine_root):
        name = item["name"].upper()
        lines.append(f"{name}_FOUND={'y' if item['found'] else 'n'}")
        lines.append(f"{name}_PATH={shlex.quote(item['rel_path'])}")
        lines.append(f"{name}_HAS_DEPLOY={'y' if item['has_deploy'] else 'n'}")
        lines.append(f"{name}_HAS_WIZARD={'y' if item['has_wizard'] else 'n'}")
        lines.append(f"{name}_REPO={shlex.quote(item['repo'])}")
        lines.append(f"{name}_ORCHESTRATE={'y' if item['orchestrate'] else 'n'}")
    return "\n".join(lines) + "\n"


def clone_kit(repo: str, dest: Path) -> str:
    """Clone a kit repo into dest. Returns 'cloned' or 'exists'."""
    if kit_is_present(dest):
        return "exists"
    if dest.exists() and any(dest.iterdir()):
        raise FileExistsError(
            f"{dest} exists but is not an Easy Deploy kit (missing {WIZARD_NAME} / {APPLY_NAME})"
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["git", "clone", "--recurse-submodules", repo, str(dest)]
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    subprocess.run(cmd, check=True, env=env)
    if not kit_is_present(dest):
        raise RuntimeError(f"cloned {repo} to {dest} but {WIZARD_NAME} / {APPLY_NAME} are missing")
    return "cloned"


def clone_named_kit(name: str, engine_root: Path = PROJECT_ROOT) -> str:
    kit = catalog_by_name(name)
    dest = kit_dest(engine_root, kit["dirname"])
    return clone_kit(str(kit["repo"]), dest)


def set_proxy_integrate(kit_root: Path) -> bool:
    """Set proxy.mode: integrate on a kit deploy.yaml. Returns True if changed."""
    deploy = kit_root / DEPLOY_NAME
    if not deploy.is_file():
        return False
    data = load_yaml(deploy)
    proxy = data.get("proxy")
    if not isinstance(proxy, dict):
        proxy = {"type": "caddy"}
        data["proxy"] = proxy
    proxy.setdefault("type", "caddy")
    if str(proxy.get("mode") or "").strip().lower() == "integrate":
        return False
    proxy["mode"] = "integrate"
    integrate = proxy.get("integrate")
    if not isinstance(integrate, dict):
        proxy["integrate"] = {"network": "easydeploy-net"}
    else:
        integrate.setdefault("network", "easydeploy-net")
    save_yaml(deploy, data)
    return True


def update_from_wizard(
    *,
    enabled: list[str],
    kits: list[dict[str, Any]],
    wire: bool,
    authorization_policy: str,
    path: Path = DEFAULT_ENGINE_PATH,
    example: Path | None = None,
) -> None:
    policy = authorization_policy.strip().lower()
    if policy not in {"one_factor", "two_factor"}:
        raise ValueError("authorization_policy must be 'one_factor' or 'two_factor'")

    config = load_or_init(path, example=example)
    config["engine"] = {"network": "easydeploy-net"}

    identity = config.get("identity")
    if not isinstance(identity, dict):
        identity = {}
        config["identity"] = identity
    identity["wire"] = "auto" if wire else False
    identity["authorization_policy"] = policy
    identity.pop("apply_kits", None)

    services = config.get("services")
    if not isinstance(services, dict):
        services = {}
        config["services"] = services

    by_name = {item["name"]: item for item in kits}
    enabled_set = set(enabled)
    for kit in KIT_CATALOG:
        name = kit["name"]
        entry = services.get(name) if isinstance(services.get(name), dict) else {}
        discovered = by_name.get(name)
        if discovered:
            entry["path"] = discovered["rel_path"]
            entry["fragment"] = discovered["fragment"]
        else:
            entry.setdefault("path", f"../{kit['dirname']}")
            entry.setdefault("fragment", kit["fragment"])
        entry["enabled"] = name in enabled_set
        services[name] = entry

    save_yaml(path, config)


def main() -> None:
    parser = argparse.ArgumentParser(description="Edit engine.yaml")
    parser.add_argument("--print-discover", action="store_true")
    parser.add_argument("--discover", action="store_true")
    parser.add_argument("--clone", metavar="KIT", help="Clone a catalog kit next to the engine")
    parser.add_argument("--path", type=Path, default=DEFAULT_ENGINE_PATH)
    parser.add_argument("--engine-root", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args()
    if args.print_discover:
        print(emit_wizard_discover(args.engine_root), end="")
        return
    if args.clone:
        try:
            print(clone_named_kit(args.clone, args.engine_root))
        except (KeyError, FileExistsError, RuntimeError, subprocess.CalledProcessError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        return
    if args.discover:
        kits = discover_kits(args.engine_root)
        print(
            json.dumps(
                [
                    {
                        "name": item["name"],
                        "label": item["label"],
                        "rel_path": item["rel_path"],
                        "has_deploy": item["has_deploy"],
                        "has_wizard": item["has_wizard"],
                        "repo": item["repo"],
                    }
                    for item in kits
                ]
            )
        )


if __name__ == "__main__":
    main()
