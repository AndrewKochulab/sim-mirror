# SPDX-License-Identifier: Apache-2.0
"""Which kind of device an identifier names, from its shape alone.

A simulator's UDID is a UUID (``D946616B-6E4F-4F5C-8C76-54FAD9B7D702``). A real device's hardware UDID is either the
eight-and-sixteen form every device since 2018 has (``00008120-0011223344556677``) or the forty hex digits older ones
had. SimMirror names a real device by its hardware UDID and never by CoreDevice's own identifier, which is a UUID and
would read as a simulator -- so the shape of a remembered identifier is enough to know which backend owns it.

An Android device has no identifier of that kind that lasts: adb's serial for an emulator is its console port, which
changes from one launch to the next, and a name may hold characters a device's id may not. So SimMirror names an
emulator by its AVD and a phone by its serial number, made to fit -- ``avd-Pixel-9-3fa2c1``, ``adb-R58M12ABC-9b1e04``:
the prefix, the name with what an id cannot hold turned into hyphens, and six hex digits of the name's SHA-256, so two
names that read alike stay apart. The Android backend finds the device again by listing, never by reading the name back.
"""

from __future__ import annotations

import hashlib
import re
from typing import Literal

from sim_mirror.protocol import DeviceKind

SIMULATOR_UDID = re.compile(r"\A[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\Z")
DEVICE_UDID = re.compile(r"\A(?:[0-9A-Fa-f]{8}-[0-9A-Fa-f]{16}|[0-9A-Fa-f]{40})\Z")
#: The longest part of an Android device's id taken from its name: the whole id stays within a claim's 64 characters.
ANDROID_SLUG_MAX = 48
ANDROID_ID = re.compile(rf"\A(?:avd|adb)-[A-Za-z0-9](?:[A-Za-z0-9-]{{0,{ANDROID_SLUG_MAX - 1}}})-[0-9a-f]{{6}}\Z")
#: What makes an Android device's id: ``avd`` for an emulator, by its AVD; ``adb`` for a phone, by its serial number.
AndroidPrefix = Literal["avd", "adb"]
EMULATOR_PREFIX: AndroidPrefix = "avd"
PHONE_PREFIX: AndroidPrefix = "adb"


def is_simulator_udid(value: object) -> bool:
    return isinstance(value, str) and bool(SIMULATOR_UDID.match(value))


def is_device_udid(value: object) -> bool:
    """Whether `value` is a real device's hardware UDID."""
    return isinstance(value, str) and bool(DEVICE_UDID.match(value))


def is_android_id(value: object) -> bool:
    """Whether `value` is an Android device's id, as `android_id` makes one."""
    return isinstance(value, str) and bool(ANDROID_ID.match(value))


def android_id(prefix: AndroidPrefix, name: str) -> str:
    """The id SimMirror knows an Android device by: an emulator by the name of its AVD, a phone by its serial number."""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", name)[:ANDROID_SLUG_MAX].strip("-") or "device"
    return f"{prefix}-{slug}-{hashlib.sha256(name.encode()).hexdigest()[:6]}"


def kind_of(value: object) -> DeviceKind | None:
    """The kind of device `value` names, or None when it names none."""
    if is_simulator_udid(value):
        return "simulator"
    if is_device_udid(value):
        return "physical"
    if is_android_id(value):
        return "android"
    return None


def build_destination(udid: str) -> str:
    """The xcodebuild ``-destination`` that builds for the device `udid` names: a simulator, else a real device."""
    platform = "iOS" if is_device_udid(udid) else "iOS Simulator"
    return f"platform={platform},id={udid}"
