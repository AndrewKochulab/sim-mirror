# SPDX-License-Identifier: Apache-2.0
"""Another simulator a test run may use: `sim_test`'s ``destination``.

A test run uses the scope's own device unless the call names another -- to run the tests on an older iOS, or on an
iPad, without changing which device the scope shows. It is named the way a person names it, ``{"name": "iPhone 17"}``
or ``{"udid": "…"}``, with ``"runtime": "iOS 26.5"`` to choose between simulators of one name, and must be one of the
iOS simulators the scope's Xcode lists (`DeviceManager.devices`). A name that matches none, or several, is refused
with the ones there are, so an agent can pick again rather than guess.

Whether the device is someone else's to use is the provider's to ask (`build/provider.py`): this only reads the call.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import NoReturn

from sim_mirror.build.xcodebuild import BuildRefused
from sim_mirror.platform.simctl import is_udid
from sim_mirror.protocol import DeviceChoice

#: The keys a destination may have.
KEYS = frozenset({"name", "udid", "runtime"})
#: The longest name or runtime a destination may give.
TEXT_MAX = 100
#: The most simulators a refusal lists; the rest are counted.
LISTED_MAX = 20

USAGE = (
    'destination is {"name": "iPhone 17"} or {"udid": "…"}, with "runtime": "iOS 26.5" to choose between '
    "simulators of one name"
)


def _text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > TEXT_MAX or not value.isprintable():
        raise BuildRefused(USAGE)
    return value.strip()


def described(choice: DeviceChoice) -> str:
    return f"{choice['name']} ({choice['runtime']})"


def _listed(choices: Sequence[DeviceChoice]) -> str:
    shown = ", ".join(described(choice) for choice in choices[:LISTED_MAX])
    more = len(choices) - LISTED_MAX
    return shown + (f" and {more} more" if more > 0 else "") if shown else "none"


def choose_destination(choices: Sequence[DeviceChoice], value: object) -> DeviceChoice:
    """The simulator a call's ``destination`` names, from the ones the scope could use. Raises `BuildRefused`."""
    if not isinstance(value, dict) or not value.keys() <= KEYS or ("name" in value) == ("udid" in value):
        raise BuildRefused(USAGE)
    name, udid, runtime = _text(value.get("name")), _text(value.get("udid")), _text(value.get("runtime"))
    if udid is not None and not is_udid(udid):
        raise BuildRefused(f"{udid!r} is not a simulator's udid; {USAGE}")
    found = [
        choice
        for choice in choices
        if (udid is None or choice["udid"].upper() == udid.upper())
        and (name is None or choice["name"] == name)
        and (runtime is None or choice["runtime"] == runtime)
    ]
    if len(found) == 1:
        return found[0]
    if found:
        _several(name or "", found)
    wanted = udid or (f"{name} ({runtime})" if runtime else name)
    raise BuildRefused(f"there is no simulator {wanted} on this Mac for this Xcode; there are {_listed(choices)}")


def _several(name: str, found: Sequence[DeviceChoice]) -> NoReturn:
    runtimes = sorted({choice["runtime"] for choice in found})
    if len(runtimes) == len(found):
        raise BuildRefused(f"several simulators are named {name}; add a runtime: {', '.join(runtimes)}")
    raise BuildRefused(
        f"several simulators are named {name} on one runtime; name one by udid: "
        + ", ".join(f"{choice['udid']} ({choice['runtime']})" for choice in found[:LISTED_MAX])
    )
