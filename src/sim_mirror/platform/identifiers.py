# SPDX-License-Identifier: Apache-2.0
"""Which kind of device an identifier names, from its shape alone.

A simulator's UDID is a UUID (``D946616B-6E4F-4F5C-8C76-54FAD9B7D702``). A real device's hardware UDID is either the
eight-and-sixteen form every device since 2018 has (``00008120-0011223344556677``) or the forty hex digits older ones
had. SimMirror names a real device by its hardware UDID and never by CoreDevice's own identifier, which is a UUID and
would read as a simulator -- so the shape of a remembered identifier is enough to know which backend owns it.
"""

from __future__ import annotations

import re

from sim_mirror.protocol import DeviceKind

SIMULATOR_UDID = re.compile(r"\A[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\Z")
DEVICE_UDID = re.compile(r"\A(?:[0-9A-Fa-f]{8}-[0-9A-Fa-f]{16}|[0-9A-Fa-f]{40})\Z")


def is_simulator_udid(value: object) -> bool:
    return isinstance(value, str) and bool(SIMULATOR_UDID.match(value))


def is_device_udid(value: object) -> bool:
    """Whether `value` is a real device's hardware UDID."""
    return isinstance(value, str) and bool(DEVICE_UDID.match(value))


def kind_of(value: object) -> DeviceKind | None:
    """The kind of device `value` names, or None when it names none."""
    if is_simulator_udid(value):
        return "simulator"
    if is_device_udid(value):
        return "physical"
    return None
