# SPDX-License-Identifier: Apache-2.0
"""What the HTTP API says about a scope's simulator: `ScopeStatus`, and the devices a picker offers."""

from __future__ import annotations

from collections.abc import Collection, Iterable

from sim_mirror.connectors.base import Capability
from sim_mirror.core.availability import Verdict
from sim_mirror.core.instance import DeviceInstance
from sim_mirror.platform.simctl import Device, runtime_label
from sim_mirror.protocol import Capability as CapabilityName
from sim_mirror.protocol import DeviceChoice, ScopeStatus


def _names(capabilities: Iterable[Capability]) -> list[CapabilityName]:
    return sorted(capability.value for capability in capabilities)


def scope_status(verdict: Verdict, instance: DeviceInstance | None, now: float) -> ScopeStatus:
    """How a scope stands: whether it can have a simulator, its device if it has one, and what a viewer is offered."""
    config = verdict.config
    selection = verdict.selection
    report = selection.report if selection is not None and selection.connector is not None else None
    if instance is not None:
        connector: str | None = instance.connector
        capabilities = instance.capabilities
        fallback = instance.fallback_reason
    else:
        connector = report.name if report is not None else None
        capabilities = report.capabilities if report is not None else frozenset()
        fallback = selection.fallback_reason if selection is not None else None
    return {
        "enabled": config.enabled,
        "reason": verdict.reason,
        "device": None if instance is None else instance.describe(now),
        "stream": {"encoding": config.stream_encoding, "fps": config.stream_fps},
        "cursor": {"enabled": config.agent_cursor, "lead_ms": config.cursor_lead_ms},
        "connector": connector,
        "capabilities": _names(capabilities),
        "fallback_reason": fallback,
    }


def device_choices(devices: Iterable[Device], created: Collection[str]) -> list[DeviceChoice]:
    """This Mac's available iOS simulators, by runtime and name, for a picker."""
    listed: list[DeviceChoice] = [
        {
            "udid": device.udid,
            "name": device.name,
            "runtime": runtime_label(device.runtime_id),
            "state": device.state,
            "created": device.udid in created,
        }
        for device in devices
        if device.available and ".iOS-" in device.runtime_id
    ]
    return sorted(listed, key=lambda choice: (choice["runtime"], choice["name"]))
