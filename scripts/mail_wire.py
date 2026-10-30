"""Wire webmail clients to the shared Stalwart mail server.

On the OpenCloud + Matrix path, webmail (Bulwark) ships inside the Stalwart kit
and needs nothing here. On the Nextcloud path, Nextcloud Mail is the webmail;
its adapter receives Stalwart's connection details as a sidecar.
"""

from __future__ import annotations

from pathlib import Path

from scripts.integrations import mail_consumers
from scripts.integrations.common import resolve_kit_path
from scripts.integrations.stalwart import read_mail_server
from scripts.stacks import resolve_stack


def wire_mail(engine_config: dict, enabled: list[dict], project_root: Path) -> list[str]:
    by_name = {item["name"]: item for item in enabled}
    consumers = [
        consumer for consumer in mail_consumers(resolve_stack(engine_config, by_name)) if consumer.kit in by_name
    ]
    if not consumers:
        return []

    stalwart = by_name.get("stalwart")
    stalwart_root = resolve_kit_path(stalwart, project_root) if stalwart else None
    notes: list[str] = []
    for consumer in consumers:
        kit_root = resolve_kit_path(by_name[consumer.kit], project_root)
        if consumer.should_skip(kit_root):
            notes.append(f"{consumer.label} wiring skipped (mail.managed: false).")
            continue
        if stalwart_root is None:
            notes.extend(consumer.unwire(kit_root))
            notes.append(f"{consumer.label}: Stalwart is not enabled here, so no mailbox is provisioned.")
            continue
        notes.extend(consumer.wire(kit_root, read_mail_server(stalwart_root)))
    return notes
