"""Normalized audit events for one Easy Deploy VPS.

The evidence is the event itself. Secret-bearing fields from the apps are
dropped or redacted before anything is written.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

GENESIS = "GENESIS"

EVENT_KEYS = (
    "ts",
    "ts_source",
    "source",
    "action",
    "outcome",
    "actor",
    "object",
    "object_id",
    "remote_ip",
    "owner",
    "message",
)

_BEARER = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{8,}")
_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|secret|token|sharetoken|client_secret)\b(\s*[=:]\s*)\S+"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def redact(text: str, secrets: tuple[str, ...] = ()) -> str:
    cleaned = _BEARER.sub(r"\1[redacted]", text or "")
    cleaned = _ASSIGNMENT.sub(r"\1\2[redacted]", cleaned)
    for secret in secrets:
        if secret and len(secret) >= 6:
            cleaned = cleaned.replace(secret, "[redacted]")
    return cleaned


def make_event(
    *,
    source: str,
    action: str,
    outcome: str = "success",
    ts: str = "",
    ts_source: str = "ingest",
    actor: str = "",
    obj: str = "",
    object_id: str = "",
    remote_ip: str = "",
    owner: str = "",
    message: str = "",
    secrets: tuple[str, ...] = (),
) -> dict[str, str]:
    when = (ts or "").strip()
    source_of_time = ts_source
    if not when:
        when = utc_now()
        source_of_time = "ingest"
    return {
        "ts": when,
        "ts_source": source_of_time,
        "source": source,
        "action": action,
        "outcome": outcome if outcome in {"success", "failure", "unknown"} else "unknown",
        "actor": actor or "",
        "object": obj or "",
        "object_id": object_id or "",
        "remote_ip": remote_ip or "",
        "owner": owner or "",
        "message": redact(message or "", secrets),
    }


def canonical(event: dict[str, Any]) -> str:
    body = {key: str(event.get(key) or "") for key in EVENT_KEYS}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def chain_hash(prev: str, event: dict[str, Any]) -> str:
    return hashlib.sha256(f"{prev}\n{canonical(event)}".encode()).hexdigest()


def dedup_id(event: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(event).encode()).hexdigest()


def parse_ts(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if "." in text:
        head, rest = text.split(".", 1)
        split_at = len(rest)
        for index, char in enumerate(rest):
            if char in "+-":
                split_at = index
                break
        fraction = "".join(char for char in rest[:split_at] if char.isdigit())[:6]
        zone = rest[split_at:]
        text = f"{head}.{fraction}{zone}" if fraction else f"{head}{zone}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_action(action: str) -> str:
    text = (action or "").strip().lower().replace("-", "_")
    text = re.sub(r"[^a-z0-9._]+", "_", text)
    return text.replace("_", ".")
