"""Map OpenCloud account ids to the usernames people sign in with.

OpenCloud's audit events name a person by ``openCloudUUID``. Kanidm calls
that same person ``owner`` or ``alice``. The evidence keeps the id. Queries
resolve the username through OpenCloud's local user directory.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.audit_store import audit_dir

CACHE_NAME = "directory.json"
CACHE_TTL_SECONDS = 600
LDAP_CONTAINER = "ldap-server"
LDAP_SEARCH = (
    "/opt/bitnami/openldap/bin/ldapsearch -o ldif-wrap=no "
    "-H ldap://127.0.0.1:1389 "
    "-D 'cn=admin,dc=opencloud,dc=eu' -w \"$LDAP_ADMIN_PASSWORD\" "
    "-b 'ou=users,dc=opencloud,dc=eu' -LLL '(objectClass=inetOrgPerson)' "
    "uid mail displayName cn openCloudUUID"
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _cache_path(root: Path) -> Path:
    return root / "state" / CACHE_NAME


def parse_ldif(text: str) -> list[dict[str, str]]:
    """Parse a single-valued LDIF search result into attribute maps."""
    entries: list[dict[str, str]] = []
    current: dict[str, str] = {}
    last_key = ""
    for raw in text.splitlines():
        if not raw.strip():
            if current:
                entries.append(current)
                current = {}
            last_key = ""
            continue
        if raw.startswith(" ") and last_key:
            current[last_key] = current.get(last_key, "") + raw[1:]
            continue
        if ":" not in raw or raw.startswith("#"):
            continue
        key, value = raw.split(":", 1)
        key = key.strip()
        if key.endswith(":"):
            continue
        last_key = key
        current[key] = value.strip()
    if current:
        entries.append(current)
    return entries


def _attr(entry: dict[str, str], *names: str) -> str:
    folded = {key.lower(): value for key, value in entry.items()}
    for name in names:
        value = folded.get(name.lower(), "").strip()
        if value:
            return value
    return ""


def users_from_ldif(text: str) -> list[dict[str, Any]]:
    users: list[dict[str, Any]] = []
    for entry in parse_ldif(text):
        name = _attr(entry, "uid", "cn")
        account_id = _attr(entry, "openCloudUUID", "entryUUID")
        mail = _attr(entry, "mail")
        display = _attr(entry, "displayName", "cn")
        if not name and not account_id:
            continue
        identifiers: list[str] = []
        for value in (account_id, name, mail):
            if value and value not in identifiers:
                identifiers.append(value)
        users.append(
            {
                "name": name or account_id,
                "display": display,
                "mail": mail,
                "ids": identifiers,
            }
        )
    return users


def _local_part(value: str) -> str:
    text = value.strip()
    if text.startswith("@") and ":" in text:
        return text[1:].split(":", 1)[0]
    if "@" in text:
        return text.split("@", 1)[0]
    return text


def _same(left: str, right: str) -> bool:
    return bool(left) and left.casefold() == right.casefold()


def identifiers_for(query: str, users: list[dict[str, Any]]) -> tuple[str, ...]:
    """Account ids and names that mean the same person as ``query``."""
    needle = (query or "").strip()
    if not needle:
        return ()
    local = _local_part(needle)
    found: list[str] = []
    for user in users:
        keys = [str(user.get("name") or ""), str(user.get("display") or ""), str(user.get("mail") or "")]
        keys.extend(str(item) for item in user.get("ids") or [])
        if not any(_same(needle, key) or _same(local, key) or _same(local, _local_part(key)) for key in keys):
            continue
        for value in user.get("ids") or []:
            text = str(value)
            if text and text not in found:
                found.append(text)
    return tuple(found)


def name_for(value: str, users: list[dict[str, Any]]) -> str:
    """Username for an OpenCloud id, when the directory knows it."""
    text = (value or "").strip()
    if not text:
        return ""
    for user in users:
        name = str(user.get("name") or "")
        keys = [name, str(user.get("mail") or "")]
        keys.extend(str(item) for item in user.get("ids") or [])
        if any(_same(text, key) for key in keys):
            return name
    return ""


def annotate_actors(rows: list[dict[str, str]], users: list[dict[str, Any]]) -> list[dict[str, str]]:
    named: list[dict[str, str]] = []
    for row in rows:
        item = dict(row)
        name = name_for(item.get("actor") or "", users)
        if name and not _same(name, item.get("actor") or ""):
            item["actor_name"] = name
        named.append(item)
    return named


def _read_cache(root: Path) -> tuple[list[dict[str, Any]], datetime | None]:
    path = _cache_path(root)
    if not path.is_file():
        return [], None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], None
    if not isinstance(payload, dict):
        return [], None
    users = payload.get("users")
    fetched = payload.get("fetched_at") or ""
    when = None
    if isinstance(fetched, str) and fetched:
        try:
            when = datetime.fromisoformat(fetched)
        except ValueError:
            when = None
    if not isinstance(users, list):
        return [], when
    return [item for item in users if isinstance(item, dict)], when


def _write_cache(root: Path, users: list[dict[str, Any]]) -> None:
    path = _cache_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"fetched_at": _now().isoformat(), "users": users}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def fetch_opencloud_users() -> list[dict[str, Any]]:
    """Read uid and openCloudUUID from the OpenCloud LDAP container."""
    result = subprocess.run(
        ["docker", "exec", LDAP_CONTAINER, "sh", "-c", LDAP_SEARCH],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "ldapsearch failed").strip()
        raise RuntimeError(detail.splitlines()[-1][:300])
    return users_from_ldif(result.stdout or "")


def directory_users(root: Path, *, refresh: bool = False) -> tuple[list[dict[str, Any]], str]:
    """Return the account directory and a warning when a refresh failed.

    A failed refresh keeps the previous cache. Queries still run.
    """
    cached, fetched = _read_cache(root)
    fresh = False
    if fetched is not None:
        age = (_now() - fetched).total_seconds()
        fresh = age >= 0 and age < CACHE_TTL_SECONDS
    if cached and fresh and not refresh:
        return cached, ""
    try:
        users = fetch_opencloud_users()
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
        if cached:
            return cached, ""
        return [], f"OpenCloud accounts could not be read ({exc}). A username will not match file-access ids."
    _write_cache(root, users)
    return users, ""
