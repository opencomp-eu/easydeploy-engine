#!/usr/bin/env python3
"""easydeploy-engine — shared Caddy orchestration."""

from __future__ import annotations

import argparse
import os
import secrets
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from scripts.embed_wire import wire_embed
from scripts.oidc_wire import resolve_kit_path, to_bool, wire_identity
from scripts.config_edit import (
    KIT_CATALOG,
    clone_named_kit,
    kit_is_present,
    load_kit_branch,
    set_proxy_integrate,
)
from scripts.progress import (
    NAME_WIDTH,
    UpdateFailed,
    combined_output,
    kit_apply_failure_message,
    kit_child_env,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "easydeploy-lib" / "python"))
import hostfs  # noqa: E402
from update_lock import (  # noqa: E402
    NOTHING_TO_UPDATE,
    UpdateSpec,
    output_reports_noop,
    record_update_lock,
    should_skip_update,
)

COMPOSE_DIR = PROJECT_ROOT / "compose"
COMPOSE_PROJECT_NAME = "easydeploy-engine"
STATE_DIR = PROJECT_ROOT / ".easydeploy-engine"
COMPOSE_ENV_PATH = STATE_DIR / "compose.env"
ENGINE_PATH = PROJECT_ROOT / "engine.yaml"
CADDY_TEMPLATE = PROJECT_ROOT / "caddy" / "Caddyfile.template"
CADDYFILE = PROJECT_ROOT / "caddy" / "Caddyfile"
DEFAULT_NETWORK = "easydeploy-net"

KNOWN_STANDALONE_CADDY_CONTAINERS = (
    "kanidm_caddy",
    "opencloud_caddy",
    "stalwart_caddy",
    "caddy",
)


@dataclass(frozen=True)
class KitRunResult:
    name: str
    status: str


@dataclass(frozen=True)
class ApplyResult:
    kits: tuple[KitRunResult, ...]
    caddy: str

    @property
    def all_noop(self) -> bool:
        if any(item.status != "nothing to update" for item in self.kits):
            return False
        return self.caddy in {"nothing to update", "skipped"}


def engine_update_spec() -> UpdateSpec:
    return UpdateSpec(
        project_root=PROJECT_ROOT,
        state_dir=STATE_DIR,
        config_paths=(ENGINE_PATH, CADDYFILE),
        compose_projects=(COMPOSE_PROJECT_NAME,),
        extra_containers=("easydeploy_caddy",),
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
    catalog_names = {kit["name"] for kit in KIT_CATALOG}
    for name, entry in services.items():
        if not isinstance(entry, dict):
            raise ValueError(f"services.{name} must be a mapping")
        if name not in catalog_names:
            print(
                f"Warning: ignoring unknown service {name!r} in engine.yaml "
                f"(not in engine catalog; remove it or set enabled: false)",
                file=sys.stderr,
            )
            continue
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
                "deploy": entry.get("deploy"),
                "oidc": entry.get("oidc") if isinstance(entry.get("oidc"), dict) else {},
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
        subprocess.run(["docker", "network", "create", name], check=True, capture_output=True)


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


def run_compose(*args: str, verbose: bool = True) -> None:
    cmd = docker_compose_cmd() + ["-f", str(COMPOSE_DIR / "docker-compose.yml")]
    overlay = STATE_DIR / "compose" / "caddy-extra.yml"
    if overlay.is_file():
        cmd.extend(["-f", str(overlay)])
    cmd.extend(args)
    env = os.environ.copy()
    env["COMPOSE_PROJECT_NAME"] = COMPOSE_PROJECT_NAME
    if not verbose:
        env.setdefault("COMPOSE_PROGRESS", "quiet")
        env.setdefault("DOCKER_CLI_HINTS", "false")
    if COMPOSE_ENV_PATH.is_file():
        for line in COMPOSE_ENV_PATH.read_text().splitlines():
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    result = subprocess.run(
        cmd,
        cwd=COMPOSE_DIR,
        capture_output=not verbose,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        detail = combined_output(result).strip() or f"exit {result.returncode}"
        raise RuntimeError(f"docker compose {' '.join(args)} failed:\n{detail}")


def collect_caddy_overlays(enabled: list[dict]) -> list[Path]:
    overlays: list[Path] = []
    for service in enabled:
        fragment_path = resolve_fragment_path(service)
        overlay = fragment_path.parent / "engine-caddy.yml"
        if overlay.is_file():
            overlays.append(overlay)
    return overlays


def assemble_caddy_runtime_overlay(enabled: list[dict]) -> Path | None:
    import shutil

    dest = STATE_DIR / "compose" / "caddy-extra.yml"
    overlays = collect_caddy_overlays(enabled)
    if not overlays:
        if dest.is_file():
            dest.unlink()
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(overlays[-1], dest)
    return dest


def resolve_operator_deploy(
    name: str, deploy_field: object, project_root: Path = PROJECT_ROOT
) -> Path | None:
    """Return the engine-owned deploy.yaml to copy into a kit, if any.

    Convention: kits/<name>.yaml is used when present, unless services.<name>.deploy
    is set to a path or explicitly false.
    """
    if deploy_field is False:
        return None
    if isinstance(deploy_field, str) and str(deploy_field).strip().lower() in {"false", "no", "0"}:
        return None
    if isinstance(deploy_field, str) and deploy_field.strip():
        path = Path(deploy_field.strip()).expanduser()
        if not path.is_absolute():
            path = (project_root / path).resolve()
        return path
    default = project_root / "kits" / f"{name}.yaml"
    if default.is_file():
        return default
    return None


def seed_kit_deploy(service: dict, project_root: Path = PROJECT_ROOT, *, verbose: bool = True) -> Path:
    """Copy operator YAML into the kit and force proxy.mode: integrate. Returns kit deploy.yaml."""
    import shutil

    kit_root = resolve_kit_path(service, project_root)
    dest = kit_root / "deploy.yaml"
    seed = resolve_operator_deploy(service["name"], service.get("deploy"), project_root)
    if seed is not None:
        if not seed.is_file():
            raise FileNotFoundError(
                f"services.{service['name']}.deploy not found: {seed}"
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(seed, dest)
        if verbose:
            print(f"Seeded {service['name']} deploy.yaml from {seed}")
    if not dest.is_file():
        raise FileNotFoundError(
            f"Missing deploy.yaml for {service['name']} at {dest}.\n"
            f"  Non-interactive: put operator config in kits/{service['name']}.yaml "
            f"(or set services.{service['name']}.deploy) and re-run apply.sh.\n"
            f"  Interactive: bash wizard.sh"
        )
    if set_proxy_integrate(kit_root) and verbose:
        print(f"Set proxy.mode: integrate on {dest}")
    return dest


def ensure_enabled_kits(
    enabled: list[dict],
    *,
    project_root: Path = PROJECT_ROOT,
    sync: bool = False,
    verbose: bool = True,
) -> list[tuple[str, str]]:
    """Clone missing catalog kits. --sync-kits also updates existing checkouts."""
    catalog = {item["name"]: item for item in KIT_CATALOG}
    branch = load_kit_branch(project_root)
    results: list[tuple[str, str]] = []
    for service in enabled:
        name = service["name"]
        dest = resolve_kit_path(service, project_root)
        present = kit_is_present(dest)
        kit = catalog.get(name)
        if present and not sync:
            continue
        if kit is None or not kit.get("orchestrate"):
            if not present:
                raise FileNotFoundError(
                    f"services.{name}: kit not found at {dest}. Clone it next to the engine."
                )
            continue
        result = clone_named_kit(name, project_root, branch=branch)
        results.append((name, result))
        if verbose:
            print(f"Kit {name}: {result} ({dest})")
    return results


def run_kit_scripts(
    enabled: list[dict],
    *,
    script_name: str = "apply.sh",
    extra_args: list[str] | None = None,
    verbose: bool = True,
    report: bool = False,
) -> list[KitRunResult]:
    """Run a kit script (apply.sh or update.sh). Kanidm first, then others."""
    extra_args = list(extra_args or [])
    order = sorted(enabled, key=lambda item: (0 if item["name"] == "kanidm" else 1, item["name"]))
    log_dir = STATE_DIR / "logs"
    results: list[KitRunResult] = []
    for service in order:
        kit_root = resolve_kit_path(service, PROJECT_ROOT)
        script = kit_root / script_name
        if not script.is_file():
            print(f"Skipping kit {script_name} for {service['name']}: {script} not found", file=sys.stderr)
            results.append(KitRunResult(service["name"], "skipped"))
            continue
        name = service["name"]
        if verbose:
            print(f"Running {script_name} for kit {name} ({kit_root})…")
        elif report:
            print(f"  {name:<{NAME_WIDTH}} …", end="", flush=True)
        result = subprocess.run(
            ["bash", str(script), *extra_args],
            cwd=kit_root,
            capture_output=True,
            text=True,
            env=kit_child_env(hostfs.isolated_child_env(), verbose=verbose),
        )
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        if verbose:
            if stdout:
                print(stdout, end="" if stdout.endswith("\n") else "\n")
            if stderr:
                print(stderr, end="" if stderr.endswith("\n") else "\n", file=sys.stderr)
        if result.returncode == 0:
            status = "nothing to update" if output_reports_noop(stdout) else "ok"
            if report:
                print(f" {status}")
            results.append(KitRunResult(name, status))
            continue
        if verbose:
            raise UpdateFailed(kit_apply_failure_message(name, "", verbose=True))
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"update-{name}.log"
        log_path.write_text(combined_output(result))
        if report:
            print(" failed")
        raise UpdateFailed(
            kit_apply_failure_message(
                name, combined_output(result), log_path=log_path, verbose=False
            )
        )
    return results


def run_kit_applies(enabled: list[dict], *, verbose: bool = True) -> list[KitRunResult]:
    """Re-apply kits so they merge identity sidecars. Kanidm first, then others."""
    if not verbose:
        print("Applying")
    return run_kit_scripts(
        enabled,
        script_name="apply.sh",
        extra_args=[],
        verbose=verbose,
        report=not verbose,
    )


def apply_engine(
    *,
    skip_runtime: bool = False,
    skip_pull: bool = False,
    apply_kits: bool = True,
    skip_kits: bool = False,
    sync_kits: bool = False,
    verbose: bool = True,
    kit_script: str = "apply.sh",
    kit_args: list[str] | None = None,
    force: bool = False,
) -> ApplyResult:
    config = load_engine()
    enabled = validate_engine(config)

    identity = config.get("identity") or {}
    should_apply_kits = (not skip_kits) and (apply_kits or to_bool(identity.get("apply_kits")))

    if should_apply_kits:
        ensure_enabled_kits(enabled, project_root=PROJECT_ROOT, sync=sync_kits, verbose=verbose)
        for service in enabled:
            seed_kit_deploy(service, PROJECT_ROOT, verbose=verbose)

    oidc_notes = wire_identity(config, enabled, PROJECT_ROOT)
    embed_notes = wire_embed(config, enabled, PROJECT_ROOT)
    if verbose:
        for line in oidc_notes:
            print(line)
        for line in embed_notes:
            print(line)

    kit_results: list[KitRunResult] = []
    if should_apply_kits:
        kit_results = run_kit_scripts(
            enabled,
            script_name=kit_script,
            extra_args=list(kit_args or []),
            verbose=verbose,
            report=False,
        )

    fragments = collect_fragments(enabled)

    CADDYFILE.parent.mkdir(parents=True, exist_ok=True)
    CADDYFILE.write_text(render_caddyfile(fragments))

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    COMPOSE_ENV_PATH.write_text(f"EDE_CADDYFILE={CADDYFILE.resolve()}\n")
    COMPOSE_ENV_PATH.chmod(0o600)

    overlay = assemble_caddy_runtime_overlay(enabled)
    if verbose:
        print(f"Rendered {CADDYFILE} from {len(fragments)} fragment(s).")
        if overlay is not None:
            print(f"Caddy compose overlay: {overlay}")

    if skip_runtime:
        return ApplyResult(kits=tuple(kit_results), caddy="skipped")

    spec = engine_update_spec()
    if not force and should_skip_update(spec, skip_pull=skip_pull, verbose=verbose):
        if verbose:
            print(NOTHING_TO_UPDATE)
        return ApplyResult(kits=tuple(kit_results), caddy="nothing to update")

    warn_standalone_caddy_conflicts()
    network = str((config.get("engine") or {}).get("network") or DEFAULT_NETWORK)
    ensure_docker_network(network)
    if overlay is not None:
        ensure_docker_network("caddy_net")

    if verbose:
        if not skip_pull:
            print("Pulling Caddy image…")
        print("Starting shared Caddy…")
    try:
        if not skip_pull:
            run_compose("pull", verbose=verbose)
        run_compose("up", "-d", "--wait", "--remove-orphans", verbose=verbose)
        reload_caddy(verbose=verbose)
    except Exception as exc:
        if not verbose:
            raise UpdateFailed(
                "caddy failed to update.\n"
                f"{exc}\n\n"
                "Re-run with more detail: bash update.sh --verbose"
            ) from exc
        raise
    record_update_lock(spec)
    if verbose:
        print()
        print("=== Easy Deploy Engine summary ===")
        print(f"Network:   {network}")
        print("Caddy:     easydeploy_caddy (ports 80/443)")
        print(f"Caddyfile: {CADDYFILE}")
        for name, path, _ in fragments:
            print(f"  - {name}: {path}")
        print()
    return ApplyResult(kits=tuple(kit_results), caddy="ok")


def reload_caddy(*, verbose: bool = True) -> None:
    """Apply Caddyfile changes; running containers do not pick up bind-mount edits automatically."""
    if subprocess.run(["docker", "inspect", "easydeploy_caddy"], capture_output=True).returncode != 0:
        return
    result = subprocess.run(
        [
            "docker",
            "exec",
            "easydeploy_caddy",
            "caddy",
            "reload",
            "--config",
            "/etc/caddy/Caddyfile",
            "--adapter",
            "caddyfile",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        if verbose:
            print("Reloaded Caddy configuration.")
        return
    detail = (result.stderr or result.stdout or "unknown error").strip()
    if verbose:
        print(f"Caddy reload failed ({detail}); recreating container…", file=sys.stderr)
    run_compose("up", "-d", "--force-recreate", "--wait", "caddy", verbose=verbose)


def ensure_backup_secret(config: dict) -> None:
    """Create the engine Borg passphrase once backup is enabled."""
    backup = config.get("backup") or {}
    if not isinstance(backup, dict) or not backup.get("enabled"):
        return
    state_secrets = STATE_DIR / "secrets.yaml"
    data: dict = {}
    if state_secrets.is_file():
        loaded = yaml.safe_load(state_secrets.read_text()) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"{state_secrets}: root must be a mapping")
        data = loaded
    if not str(data.get("BORG_PASSPHRASE") or "").strip():
        data["BORG_PASSPHRASE"] = secrets.token_hex(32)
        state_secrets.parent.mkdir(parents=True, exist_ok=True)
        state_secrets.write_text(yaml.safe_dump(data, default_flow_style=False, sort_keys=False))
        state_secrets.chmod(0o600)
        print(f"Generated Borg passphrase in {state_secrets}")


def reconcile_backup_schedule() -> None:
    """Reconcile the engine systemd timer from engine.yaml."""
    schedule_script = PROJECT_ROOT / "easydeploy-lib" / "python" / "backup_schedule.py"
    sys.path.insert(0, str(PROJECT_ROOT / "easydeploy-lib" / "python"))
    try:
        from backup_plan import load_plan

        timer_name = load_plan(PROJECT_ROOT)["timer_name"]
    finally:
        sys.path.pop(0)
    subprocess.run(
        [
            sys.executable,
            str(schedule_script),
            "--project-root",
            str(PROJECT_ROOT),
            "--deploy-yaml",
            str(ENGINE_PATH),
            "--unit-name",
            timer_name,
        ],
        check=True,
        text=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply easydeploy-engine configuration")
    parser.add_argument("--skip-runtime", action="store_true")
    parser.add_argument("--skip-pull", action="store_true")
    parser.add_argument(
        "--skip-kits",
        action="store_true",
        help="Only assemble Caddy and identity sidecars (do not clone, seed, or apply kits)",
    )
    parser.add_argument(
        "--sync-kits",
        action="store_true",
        help="git fetch/checkout engine.kit_branch on existing kit clones before apply",
    )
    parser.add_argument(
        "--apply-kits",
        action="store_true",
        help="Deprecated: kit apply is now the default. Use --skip-kits to skip.",
    )
    args = parser.parse_args()
    try:
        config = load_engine()
        ensure_backup_secret(config)
        apply_engine(
            skip_runtime=args.skip_runtime,
            skip_pull=args.skip_pull,
            apply_kits=True,
            skip_kits=args.skip_kits,
            sync_kits=args.sync_kits,
        )
        reconcile_backup_schedule()
    except UpdateFailed as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
    except (FileNotFoundError, ValueError, RuntimeError, PermissionError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
