"""Interfaces for wiring kits to the shared core (Kanidm identity, Stalwart mail).

An ``IdentityConsumer`` adapts one kit to Kanidm. The engine calls it with a
``WireContext``; the adapter writes sidecars (a Kanidm OAuth2 client
definition plus whatever the kit merges on apply) and returns status lines.

A ``MailConsumer`` is a kit that reads and sends mail through Stalwart (a
webmail client). The mail kit describes itself once as a ``MailServer``.

Adapters never edit an operator's deploy.yaml.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from scripts.integrations.common import KANIDM_STATE, resolve_kit_path, write_sidecar


@dataclass
class WireContext:
    engine_config: dict
    enabled: dict[str, dict]
    project_root: Path
    kanidm_root: Path
    kanidm_domain: str
    consumers: dict = field(default_factory=dict)

    @property
    def clients_dir(self) -> Path:
        return self.kanidm_root / KANIDM_STATE / "integration" / "oidc-clients.d"

    def service(self, kit: str) -> dict | None:
        return self.enabled.get(kit)

    def kit_root(self, kit: str) -> Path | None:
        service = self.service(kit)
        return resolve_kit_path(service, self.project_root) if service else None

    def consumer_cfg(self, kit: str) -> dict:
        """identity.consumers.<kit> in engine.yaml: overrides and split-VPS declarations."""
        entry = self.consumers.get(kit) or {}
        return entry if isinstance(entry, dict) else {}

    def write_client(self, client: dict) -> Path:
        path = self.clients_dir / f"{client['client_id']}.yaml"
        write_sidecar(path, client)
        return path


class IdentityConsumer(ABC):
    """One kit that signs users in through Kanidm."""

    kit: str
    label: str
    default_client_id: str
    # Shown when the kit is enabled but opted out of engine wiring.
    skip_note: str

    def client_id(self, ctx: WireContext) -> str:
        client_id = self.default_client_id
        service = ctx.service(self.kit)
        if service and isinstance(service.get("oidc"), dict):
            client_id = str(service["oidc"].get("client_id") or "").strip() or client_id
        return str(ctx.consumer_cfg(self.kit).get("client_id") or "").strip() or client_id

    @abstractmethod
    def should_skip(self, kit_root: Path, service: dict) -> bool:
        """True when the operator opted this kit out of engine-managed identity."""

    @abstractmethod
    def wire_local(self, ctx: WireContext, kit_root: Path) -> list[str]:
        """Wire a kit enabled on this VPS. Returns status lines."""

    def wire_remote(self, ctx: WireContext) -> list[str] | None:
        """Register a client for a kit on another host (split-VPS). None when not declared."""
        return None

    def wire(self, ctx: WireContext) -> tuple[bool, list[str]]:
        """Returns (wired, notes)."""
        kit_root = ctx.kit_root(self.kit)
        service = ctx.service(self.kit)
        if kit_root is not None and service is not None and not self.should_skip(kit_root, service):
            return True, self.wire_local(ctx, kit_root)
        remote = self.wire_remote(ctx)
        if remote is not None:
            return True, remote
        if service is not None:
            return False, [self.skip_note]
        return False, []


@dataclass(frozen=True)
class MailServer:
    """How a mail client on this VPS reaches the mail kit."""

    hostname: str
    email_domain: str
    imap_port: int
    submission_port: int
    sieve_port: int
    # A server account allowed to act for any mailbox. Clients log in as
    # "<mailbox><separator><master_user>" with the master user's password.
    master_user: str
    master_user_separator: str
    # Secret ref ({file, key}) the consuming kit resolves at its apply time.
    master_password: dict[str, str]


class MailConsumer(ABC):
    """One kit that is a mail client of the shared mail server."""

    kit: str
    label: str

    @abstractmethod
    def should_skip(self, kit_root: Path) -> bool:
        """True when the operator opted this kit out of engine-managed mail."""

    @abstractmethod
    def wire(self, kit_root: Path, server: MailServer) -> list[str]:
        """Write the kit's mail sidecar. Returns status lines."""

    @abstractmethod
    def unwire(self, kit_root: Path) -> list[str]:
        """Remove the mail sidecar when no mail server is enabled."""
