# SPDX-License-Identifier: Apache-2.0
"""How a device looks and where it believes it is, changed by name: an agent's `sim_device`, a person's viewer and
``sim-mirror device`` all ask for a change the same way.

Each change is checked here -- its arguments, and what it needs of the device -- and made through the device's change
ledger (`core.device_changes`), so a real device gets back what it had when it is let go.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from sim_mirror.connectors.base import Capability
from sim_mirror.core.device_changes import Changes
from sim_mirror.platform.simctl import CONTENT_SIZES

#: The most points a simulated route may pass through.
WAYPOINTS_MAX = 100
#: How fast a simulated route moves, in metres a second: from walking slowly to flying.
SPEED_BOUNDS = (0.5, 300)


class SettingRefused(ValueError):
    """A change asked for wrongly, said so whoever asked can ask again."""


#: What each change needs of the device, and how it is made: the arguments, checked, then the change.
Change = Callable[[dict[str, Any], Changes], tuple[str, Awaitable[None]]]


def _switch(args: dict[str, Any], action: str) -> bool:
    on = args.get("on")
    if not isinstance(on, bool):
        raise SettingRefused(f"{action} takes on: true or false")
    return on


def _degrees(value: object, limit: int, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not -limit <= value <= limit:
        raise SettingRefused(f"{name} is a number from -{limit} to {limit}")
    return float(value)


def _place(point: object) -> tuple[float, float]:
    if not isinstance(point, list) or len(point) != 2:
        raise SettingRefused("each waypoint is [latitude, longitude]")
    return _degrees(point[0], 90, "latitude"), _degrees(point[1], 180, "longitude")


def _appearance(args: dict[str, Any], changes: Changes) -> tuple[str, Awaitable[None]]:
    mode = args.get("mode")
    if mode not in ("light", "dark"):
        raise SettingRefused("appearance takes a mode of light or dark")
    return f"appearance {mode}", changes.appearance(mode)


def _status_bar(args: dict[str, Any], changes: Changes) -> tuple[str, Awaitable[None]]:
    preset = args.get("preset")
    if preset not in ("demo", "clear"):
        raise SettingRefused("status_bar takes a preset of demo or clear")
    said = "demo status bar" if preset == "demo" else "the device's own status bar"
    return said, changes.status_bar(preset == "demo")


def _location(args: dict[str, Any], changes: Changes) -> tuple[str, Awaitable[None]]:
    waypoints = args.get("waypoints")
    if waypoints is None:
        latitude = _degrees(args.get("latitude"), 90, "latitude")
        longitude = _degrees(args.get("longitude"), 180, "longitude")
        return f"located at {latitude:g}, {longitude:g}", changes.locate(latitude, longitude)
    if not isinstance(waypoints, list) or not 2 <= len(waypoints) <= WAYPOINTS_MAX:
        raise SettingRefused(f"waypoints are 2 to {WAYPOINTS_MAX} [latitude, longitude] pairs")
    speed = args.get("speed", 20)
    low, high = SPEED_BOUNDS
    if isinstance(speed, bool) or not isinstance(speed, int | float) or not low <= speed <= high:
        raise SettingRefused(f"speed is metres a second, from {low:g} to {high:g}")
    points = [_place(point) for point in waypoints]
    return f"moving along {len(points)} waypoints at {speed:g} m/s", changes.route(points, float(speed))


def _clear_location(args: dict[str, Any], changes: Changes) -> tuple[str, Awaitable[None]]:
    return "location cleared", changes.clear_location()


def _text_size(args: dict[str, Any], changes: Changes) -> tuple[str, Awaitable[None]]:
    size = args.get("size")
    if size not in CONTENT_SIZES:
        raise SettingRefused(f"text_size takes a size: {', '.join(CONTENT_SIZES)}")
    return f"text size {size}", changes.text_size(size)


def _contrast(args: dict[str, Any], changes: Changes) -> tuple[str, Awaitable[None]]:
    on = _switch(args, "contrast")
    return f"increased contrast {'on' if on else 'off'}", changes.contrast(on)


def _reduce_motion(args: dict[str, Any], changes: Changes) -> tuple[str, Awaitable[None]]:
    on = _switch(args, "reduce_motion")
    return f"reduce motion {'on' if on else 'off'}", changes.reduce_motion(on)


CHANGES: dict[str, tuple[Capability, Change]] = {
    "appearance": (Capability.APPEARANCE, _appearance),
    "status_bar": (Capability.STATUS_BAR, _status_bar),
    "location": (Capability.LOCATION, _location),
    "clear_location": (Capability.LOCATION, _clear_location),
    "text_size": (Capability.ACCESSIBILITY, _text_size),
    "contrast": (Capability.ACCESSIBILITY, _contrast),
    "reduce_motion": (Capability.ACCESSIBILITY, _reduce_motion),
}
