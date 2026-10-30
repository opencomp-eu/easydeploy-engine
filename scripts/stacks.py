"""Deployment paths: which collaboration suite runs on the shared identity and mail core.

Every workspace runs exactly one path. Kanidm (identity) and Stalwart (mail) are
the core every path shares. Each path adds its own collaboration kits on top:

- ``opencloud-matrix``: OpenCloud (files, office) and Matrix (chat, calls), with
  Bulwark as webmail.
- ``nextcloud``: Nextcloud (files, office, Talk, calendar, contacts, and mail
  through Nextcloud Mail against Stalwart). Bulwark is not deployed.

Retiring a path means deleting its ``Stack`` below, its kits from
``config_edit.KIT_CATALOG``, and its adapters under ``scripts/integrations/``.
Nothing else branches on the path name.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

CORE_KITS: tuple[str, ...] = ("kanidm", "stalwart")


@dataclass(frozen=True)
class Stack:
    name: str
    label: str
    summary: str
    # Collaboration kits that belong to this path only.
    suite: tuple[str, ...]
    # Every kit this path may enable, in apply order. Kanidm must come first so
    # OAuth2 clients and their secrets exist before any consumer applies.
    apply_order: tuple[str, ...]
    # deploy.yaml values the engine enforces on a kit when it runs on this path.
    kit_policy: Mapping[str, Mapping[str, Any]] = field(
        default_factory=lambda: MappingProxyType({})
    )

    @property
    def kits(self) -> frozenset[str]:
        return frozenset(self.apply_order)

    def policy_for(self, kit: str) -> Mapping[str, Any]:
        return self.kit_policy.get(kit) or {}


OPENCLOUD_MATRIX = Stack(
    name="opencloud-matrix",
    label="OpenCloud + Matrix",
    summary="OpenCloud files and office, Matrix chat and calls, Bulwark webmail",
    suite=("opencloud", "matrix"),
    apply_order=("kanidm", "matrix", "opencloud", "stalwart"),
)

NEXTCLOUD = Stack(
    name="nextcloud",
    label="Nextcloud",
    summary="Nextcloud files, Collabora office, Talk, calendar, contacts, and Nextcloud Mail",
    suite=("nextcloud",),
    # Stalwart applies before Nextcloud so its recovery admin secret exists when
    # Nextcloud Mail provisioning is configured.
    apply_order=("kanidm", "stalwart", "nextcloud"),
    kit_policy=MappingProxyType({"stalwart": MappingProxyType({"bulwark": {"enabled": False}})}),
)

STACKS: Mapping[str, Stack] = MappingProxyType(
    {stack.name: stack for stack in (OPENCLOUD_MATRIX, NEXTCLOUD)}
)
DEFAULT_STACK = OPENCLOUD_MATRIX.name


def get_stack(name: str) -> Stack:
    key = str(name or "").strip().lower()
    if key not in STACKS:
        options = ", ".join(sorted(STACKS))
        raise ValueError(f"engine.stack must be one of: {options} (got {name!r})")
    return STACKS[key]


def stack_owning(kit: str) -> Stack | None:
    """The path a suite kit belongs to; None for core kits."""
    for stack in STACKS.values():
        if kit in stack.suite:
            return stack
    return None


def infer_stack(kit_names: Iterable[str]) -> Stack:
    """The path implied by enabled kits. Core-only workspaces use the default path."""
    owners = {owner.name for name in kit_names if (owner := stack_owning(name)) is not None}
    if len(owners) > 1:
        raise ValueError(_mixed_paths_message(kit_names))
    if owners:
        return STACKS[owners.pop()]
    return STACKS[DEFAULT_STACK]


def resolve_stack(config: dict, kit_names: Iterable[str]) -> Stack:
    """engine.stack when set, else inferred. Enabled kits must all belong to it."""
    names = list(kit_names)
    engine = config.get("engine") if isinstance(config.get("engine"), dict) else {}
    raw = str((engine or {}).get("stack") or "").strip()
    stack = get_stack(raw) if raw else infer_stack(names)
    foreign = [name for name in names if name not in stack.kits]
    if foreign:
        if raw:
            owners = ", ".join(
                f"{name} ({owner.label})" if (owner := stack_owning(name)) else name
                for name in foreign
            )
            raise ValueError(
                f"engine.stack is {stack.name!r} ({stack.label}), but these services belong to "
                f"another path: {owners}. Disable them or change engine.stack."
            )
        raise ValueError(_mixed_paths_message(names))
    return stack


def apply_order(kit_names: Iterable[str], stack: Stack | None = None) -> list[str]:
    """Kit names sorted by the path's apply order (Kanidm first)."""
    names = list(kit_names)
    chosen = stack or infer_stack(names)
    rank = {name: index for index, name in enumerate(chosen.apply_order)}
    return sorted(names, key=lambda name: (rank.get(name, len(rank)), name))


def _mixed_paths_message(kit_names: Iterable[str]) -> str:
    by_path: dict[str, list[str]] = {}
    for name in kit_names:
        owner = stack_owning(name)
        if owner is not None:
            by_path.setdefault(owner.label, []).append(name)
    detail = "; ".join(f"{label}: {', '.join(sorted(kits))}" for label, kits in sorted(by_path.items()))
    return (
        "A workspace runs one collaboration path, but services from several are enabled "
        f"({detail}). Kanidm and Stalwart work with either path."
    )
