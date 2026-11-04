#!/usr/bin/env python3
"""Create a Stalwart app password for a Kanidm person.

Webmail signs in with Kanidm. Phone and desktop mail apps need a Stalwart app
password instead. This looks up the person's mail address in Kanidm and asks
the Stalwart kit to mint the secret.

Examples:
  bash app-password.sh alice
  bash app-password.sh alice --description "iPhone"
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import yaml

from scripts.config_edit import catalog_by_name, describe_kit
from scripts.integrations.common import resolve_kit_path, to_bool
from scripts.integrations.stalwart import read_stalwart_domains

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENGINE_PATH = PROJECT_ROOT / "engine.yaml"

Runner = Callable[..., subprocess.CompletedProcess]


def load_engine(path: Path = ENGINE_PATH) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(
            f"Missing {path.name}. Run bash wizard.sh (or copy engine.yaml.example) first."
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("engine.yaml root must be a mapping")
    return data


def enabled_service(engine: dict[str, Any], name: str) -> dict[str, Any]:
    services = engine.get("services") if isinstance(engine.get("services"), dict) else {}
    entry = services.get(name) if isinstance(services.get(name), dict) else {}
    if not to_bool(entry.get("enabled", False)):
        raise RuntimeError(
            f"services.{name}.enabled is false in engine.yaml. "
            "Enable that kit before creating a mail app password."
        )
    return entry


def kit_root(engine: dict[str, Any], name: str, project_root: Path = PROJECT_ROOT) -> Path:
    try:
        return resolve_kit_path(enabled_service(engine, name), project_root)
    except (KeyError, TypeError, ValueError):
        pass
    described = describe_kit(catalog_by_name(name), project_root)
    root = Path(described["path"])
    if not described["found"] or not (root / "deploy.yaml").is_file():
        raise FileNotFoundError(
            f"Could not find {name}-easy-deploy next to the engine. "
            "Run bash wizard.sh or set services.{name}.path in engine.yaml."
        )
    return root


def kit_python_command(kit_root_path: Path, module: str, args: list[str]) -> list[str]:
    python = kit_root_path / ".venv" / "bin" / "python"
    if python.is_file():
        return [str(python), "-m", module, *args]
    uv = shutil.which("uv") or "uv"
    return [uv, "run", "--project", str(kit_root_path), "python", "-m", module, *args]


def run_kit_module(
    kit_root_path: Path,
    module: str,
    args: list[str],
    *,
    runner: Runner = subprocess.run,
) -> subprocess.CompletedProcess[str]:
    return runner(
        kit_python_command(kit_root_path, module, args),
        cwd=kit_root_path,
        capture_output=True,
        text=True,
        check=False,
    )


def choose_mailbox_email(emails: list[str], mail_domain: str) -> str:
    """Prefer an address on the Stalwart mail domain."""
    addresses = [email.strip().casefold() for email in emails if email and email.strip()]
    if not addresses:
        raise RuntimeError("Kanidm person has no mail address")
    suffix = "@" + mail_domain.strip().casefold().lstrip("@")
    matches = [email for email in addresses if email.endswith(suffix)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise RuntimeError(
            f"Several mail addresses on @{mail_domain}: {', '.join(matches)}. "
            "Pass --email to pick one."
        )
    if len(addresses) == 1:
        raise RuntimeError(
            f"Kanidm mail is {addresses[0]!r}, but Stalwart's domain is {mail_domain!r}. "
            "Pass --email with the mailbox Stalwart should use, or fix the person's mail attribute."
        )
    raise RuntimeError(
        f"No Kanidm mail on @{mail_domain} (have {', '.join(addresses)}). "
        "Pass --email or update the person in Kanidm."
    )


def person_emails(
    kanidm_root: Path,
    username: str,
    *,
    runner: Runner = subprocess.run,
) -> list[str]:
    result = run_kit_module(
        kanidm_root,
        "scripts.user",
        ["mail", username, "--json"],
        runner=runner,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip() or f"exit {result.returncode}"
        raise RuntimeError(f"Could not read Kanidm person {username!r}: {detail}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Kanidm mail lookup returned no JSON for {username!r}: {(result.stdout or '')[:200]}"
        ) from exc
    emails = payload.get("emails") if isinstance(payload, dict) else None
    if not isinstance(emails, list) or not emails:
        raise RuntimeError(f"Kanidm person {username!r} has no mail attribute")
    return [str(item) for item in emails]


def create_stalwart_app_password(
    stalwart_root: Path,
    mailbox: str,
    *,
    description: str,
    runner: Runner = subprocess.run,
) -> dict[str, str]:
    result = run_kit_module(
        stalwart_root,
        "scripts.app_password",
        [mailbox, "--description", description, "--json"],
        runner=runner,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip() or f"exit {result.returncode}"
        raise RuntimeError(detail)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Stalwart did not return JSON: {(result.stdout or '')[:200]}"
        ) from exc
    email = str(payload.get("email") or "").strip()
    secret = str(payload.get("secret") or "").strip()
    if not email or not secret:
        raise RuntimeError(f"Stalwart response was incomplete: {payload!r}")
    return {"email": email, "secret": secret}


def create_for_username(
    username: str,
    *,
    description: str = "Mail client",
    email: str = "",
    engine: dict[str, Any] | None = None,
    project_root: Path = PROJECT_ROOT,
    runner: Runner = subprocess.run,
) -> dict[str, str]:
    cfg = engine if engine is not None else load_engine(project_root / "engine.yaml")
    kanidm_root = kit_root(cfg, "kanidm", project_root)
    stalwart_root = kit_root(cfg, "stalwart", project_root)
    _hostname, mail_domain = read_stalwart_domains(stalwart_root)
    mailbox = email.strip().casefold()
    if not mailbox:
        mailbox = choose_mailbox_email(person_emails(kanidm_root, username, runner=runner), mail_domain)
    return create_stalwart_app_password(
        stalwart_root,
        mailbox,
        description=description,
        runner=runner,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create a Stalwart app password for a Kanidm person"
    )
    parser.add_argument("username", help="Kanidm username (for example alice)")
    parser.add_argument(
        "--description",
        default="Mail client",
        help="Label shown in Stalwart Account → Credentials → App Passwords",
    )
    parser.add_argument(
        "--email",
        default="",
        help="Mailbox address when the person has several, or when Kanidm mail is wrong",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print {\"email\", \"secret\"} instead of plain text",
    )
    args = parser.parse_args(argv)
    try:
        created = create_for_username(
            args.username,
            description=args.description,
            email=args.email,
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(created))
    else:
        print(f"Kanidm user: {args.username}")
        print(f"Mailbox: {created['email']}")
        print(f"App password: {created['secret']}")
        print("Copy it now; Stalwart will not show it again.")
        print("IMAP mail.<domain>:993  SMTP :465 or :587  username = mailbox address")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
