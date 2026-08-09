#!/usr/bin/env python3
"""easydeploy-engine — shared Caddy orchestration."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
COMPOSE_DIR = PROJECT_ROOT / "compose"
STATE_DIR = PROJECT_ROOT / ".easydeploy-engine"
COMPOSE_ENV_PATH = STATE_DIR / "compose.env"
ENGINE_PATH = PROJECT_ROOT / "engine.yaml"
CADDY_TEMPLATE = PROJECT_ROOT / "caddy" / "Caddyfile.template"
CADDYFILE = PROJECT_ROOT / "caddy" / "Caddyfile"
DEFAULT_NETWORK = "easydeploy-net"

KNOWN_STANDALONE_CADDY_CONTAINERS = (
    "authelia_caddy",
    "opencloud_caddy",
    "caddy",
)


def load_yaml(path: Path) -> dict:
    with path.open() as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: root must be a mapping")
    return data


def load_engine(path: Path = ENGINE_PATH) -> dict:
    if not path.exists():
        raise FileNotFoundError(
            f"Missing {path.name}. Copy engine.yaml.example to engine.yaml and enable services."
        )
    return load_yaml(path)


def validate_engine(config: dict) -> list[dict]:
    engine = config.get("engine") or {}
    network = str(engine.get("network") or DEFAULT_NETWORK).strip()
    if network != DEFAULT_NETWORK:
        raise ValueError(
            f"engine.network must be {DEFAULT_NETWORK!r} in MVP (got {network!r})"
        )

    services = config.get("services") or {}
    if not isinstance(services, dict):
        raise ValueError("services must be a mapping")

    enabled: list[dict] = []
    for name, entry in services.items():
        if not isinstance(entry, dict):
            raise ValueError(f"services.{name} must be a mapping")
        if not entry.get("enabled"):
            continue
        kit_path = str(entry.get("path") or "").strip()
        fragment_rel = str(entry.get("fragment") or "").strip()
        if not kit_path:
            raise ValueError(f"services.{name}.path is required when enabled")
        if not fragment_rel:
            raise ValueError(f"services.{name}.fragment is required when enabled")
        enabled.append(
            {
                "name": name,
                "path": Path(kit_path).expanduser(),
                "fragment_rel": fragment_rel,
            }
        )

    if not enabled:
        raise ValueError("Enable at least one service in engine.yaml")

    return enabled


def resolve_fragment_path(service: dict) -> Path:
    base = service["path"]
    if not base.is_absolute():
        base = (PROJECT_ROOT / base).resolve()
    return (base / service["fragment_rel"]).resolve()


def collect_fragments(enabled: list[dict]) -> list[tuple[str, Path, str]]:
    collected: list[tuple[str, Path, str]] = []
    missing: list[str] = []
    for service in sorted(enabled, key=lambda item: item["name"]):
        fragment_path = resolve_fragment_path(service)
        if not fragment_path.is_file():
            missing.append(f"{service['name']}: {fragment_path}")
            continue
        text = fragment_path.read_text().strip()
        if not text:
            missing.append(f"{service['name']}: empty fragment at {fragment_path}")
            continue
        collected.append((service["name"], fragment_path, text))
    if missing:
        lines = "\n".join(f"  - {line}" for line in missing)
        raise FileNotFoundError(
            "Missing or empty Caddy fragments (run apply in each kit with proxy.mode: integrate):\n"
            f"{lines}"
        )
    return collected


def render_caddyfile(fragments: list[tuple[str, Path, str]]) -> str:
    blocks: list[str] = []
    for name, path, text in fragments:
        blocks.append(f"# --- {name} (from {path}) ---\n{text}")
    site_blocks = "\n\n".join(blocks)
    template = CADDY_TEMPLATE.read_text()
    if "{{SITE_BLOCKS}}" not in template:
        raise ValueError("Caddyfile.template missing {{SITE_BLOCKS}}")
    return template.replace("{{SITE_BLOCKS}}", site_blocks) + "\n"


def warn_standalone_caddy_conflicts() -> None:
    running: list[str] = []
    for name in KNOWN_STANDALONE_CADDY_CONTAINERS:
        if subprocess.run(["docker", "inspect", name], capture_output=True).returncode == 0:
            running.append(name)
    if not running:
        return
    print(
        "Warning: standalone Caddy containers still present (may conflict on :443): "
        + ", ".join(running),
        file=sys.stderr,
    )
    print(
        "  Stop them or switch those kits to proxy.mode: integrate before using the engine.",
        file=sys.stderr,
    )


def ensure_docker_network(name: str) -> None:
    if subprocess.run(["docker", "network", "inspect", name], capture_output=True).returncode != 0:
        subprocess.run(["docker", "network", "create", name], check=True)


def docker_compose_cmd() -> list[str]:
    import shutil

    if shutil.which("docker"):
        result = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True)
        if result.returncode == 0:
            return ["docker", "compose"]
    compose = shutil.which("docker-compose")
    if compose:
        return [compose]
    raise RuntimeError("Docker Compose v2 is required")


def run_compose(*args: str) -> None:
    cmd = docker_compose_cmd() + ["-f", str(COMPOSE_DIR / "docker-compose.yml"), *args]
    env = os.environ.copy()
    if COMPOSE_ENV_PATH.is_file():
        for line in COMPOSE_ENV_PATH.read_text().splitlines():
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    subprocess.run(cmd, cwd=COMPOSE_DIR, check=True, env=env)


def apply_engine(*, skip_runtime: bool = False, skip_pull: bool = False) -> None:
    config = load_engine()
    enabled = validate_engine(config)
    fragments = collect_fragments(enabled)

    CADDYFILE.parent.mkdir(parents=True, exist_ok=True)
    CADDYFILE.write_text(render_caddyfile(fragments))

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    COMPOSE_ENV_PATH.write_text(f"EDE_CADDYFILE={CADDYFILE.resolve()}\n")
    COMPOSE_ENV_PATH.chmod(0o600)

    print(f"Rendered {CADDYFILE} from {len(fragments)} fragment(s).")

    if skip_runtime:
        return

    warn_standalone_caddy_conflicts()
    network = str((config.get("engine") or {}).get("network") or DEFAULT_NETWORK)
    ensure_docker_network(network)

    if not skip_pull:
        print("Pulling Caddy image…")
        run_compose("pull")

    print("Starting shared Caddy…")
    run_compose("up", "-d", "--wait", "--remove-orphans")

    print()
    print("=== Easy Deploy Engine summary ===")
    print(f"Network:   {network}")
    print(f"Caddy:     easydeploy_caddy (ports 80/443)")
    print(f"Caddyfile: {CADDYFILE}")
    for name, path, _ in fragments:
        print(f"  - {name}: {path}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply easydeploy-engine configuration")
    parser.add_argument("--skip-runtime", action="store_true")
    parser.add_argument("--skip-pull", action="store_true")
    args = parser.parse_args()
    try:
        apply_engine(skip_runtime=args.skip_runtime, skip_pull=args.skip_pull)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
