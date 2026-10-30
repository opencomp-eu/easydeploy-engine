"""Integration adapters between kits and the shared core.

Add a kit: write an adapter module and register it below. Retire a path:
delete its adapter modules and their entries here (see scripts/stacks.py).
"""

from __future__ import annotations

from scripts.integrations.base import IdentityConsumer, MailConsumer, MailServer, WireContext
from scripts.integrations.matrix import MatrixConsumer
from scripts.integrations.nextcloud import NextcloudConsumer, NextcloudMail
from scripts.integrations.opencloud import OpenCloudConsumer
from scripts.integrations.stalwart import StalwartConsumer
from scripts.stacks import Stack

IDENTITY_CONSUMERS: tuple[IdentityConsumer, ...] = (
    OpenCloudConsumer(),
    MatrixConsumer(),
    StalwartConsumer(),
    NextcloudConsumer(),
)

MAIL_CONSUMERS: tuple[MailConsumer, ...] = (NextcloudMail(),)


def identity_consumers(stack: Stack) -> tuple[IdentityConsumer, ...]:
    return tuple(consumer for consumer in IDENTITY_CONSUMERS if consumer.kit in stack.kits)


def mail_consumers(stack: Stack) -> tuple[MailConsumer, ...]:
    return tuple(consumer for consumer in MAIL_CONSUMERS if consumer.kit in stack.kits)


__all__ = [
    "IDENTITY_CONSUMERS",
    "MAIL_CONSUMERS",
    "IdentityConsumer",
    "MailConsumer",
    "MailServer",
    "WireContext",
    "identity_consumers",
    "mail_consumers",
]
