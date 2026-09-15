# SPDX-License-Identifier: Apache-2.0
"""Which simulator a scope uses, and remembering it.

``device.mode`` decides what is remembered: ``per_scope`` gives every scope a device of its own, so two scopes never tap
on each other's apps; ``shared`` gives a whole group one. The answer is kept by UDID in a host's `DeviceMemory` -- for a
standalone install, `JsonDeviceMemory`, a private file in SimMirror's state folder -- so a scope finds its device again
after a restart, and a device a person picks stays picked.

A remembered device that no longer exists -- deleted in Xcode, its runtime removed -- is replaced the next time the
scope asks. A device SimMirror made is listed as made, and is deleted only when a person asks for that: switching the
simulator off touches nothing on disk.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from sim_mirror.config.model import SimConfig
from sim_mirror.host_copy import HostCopy
from sim_mirror.platform.simctl import DeviceType, Runtime, Simctl, runtime_label
from sim_mirror.scope import Scope
from sim_mirror.seams import DeviceMemory, StateStore

#: How the device a group shares is remembered beside the ones scopes have of their own.
SHARED_PREFIX = "group:"


class NoDevice(Exception):
    """No device can be had for a scope, with why -- a status 409 carries to a viewer."""

    status = 409


@dataclass(frozen=True)
class DeviceRef:
    udid: str
    name: str
    runtime: str
    #: Whether SimMirror made it (and so may delete it when a person asks).
    created: bool


def pick_runtime(runtimes: list[Runtime], wanted: str) -> Runtime | None:
    """The iOS runtime to use: the one named (by name or version), else the newest installed."""
    ios = [runtime for runtime in runtimes if runtime.available and runtime.platform == "iOS"]
    if wanted:
        ios = [runtime for runtime in ios if wanted in (runtime.name, runtime.version)]
    return max(ios, key=lambda runtime: runtime.version_key, default=None)


def pick_device_type(runtime: Runtime, wanted: str) -> DeviceType | None:
    """The device type to make: the one named, else the newest iPhone the runtime runs.

    simctl lists a runtime's device types newest first, so the first iPhone is the newest one.
    """
    if wanted:
        return next((kind for kind in runtime.device_types if kind.name == wanted), None)
    phones = [kind for kind in runtime.device_types if kind.product_family == "iPhone"]
    return next(iter(phones or runtime.device_types), None)


def device_name(prefix: str, scope: Scope, shared: bool) -> str:
    """What a device SimMirror makes is called in Xcode's list: the prefix, then the group it is shared by or the
    scope it is for."""
    return f"{prefix} · {scope.group if shared else scope.label}"


class JsonDeviceMemory:
    """A standalone install's `DeviceMemory`: one private JSON file in the state store."""

    def __init__(self, state: StateStore) -> None:
        self._state = state

    @staticmethod
    def _key(scope: Scope, shared: bool) -> str:
        return f"{SHARED_PREFIX}{scope.group}" if shared else scope.id

    def _read(self, scope: Scope) -> dict[str, Any]:
        try:
            data = json.loads(self._state.devices_file(scope).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        scopes = data.get("scopes") if isinstance(data, dict) else None
        created = data.get("created") if isinstance(data, dict) else None
        return {
            "scopes": {str(k): str(v) for k, v in scopes.items()} if isinstance(scopes, dict) else {},
            "created": sorted({str(u) for u in created}) if isinstance(created, list) else [],
        }

    def _write(self, scope: Scope, data: dict[str, Any]) -> None:
        path = self._state.devices_file(scope)
        folder = self._state.ensure_dir(path.parent)
        temporary = folder / f".{path.name}.{os.getpid()}.tmp"
        temporary.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)

    def assigned(self, scope: Scope, shared: bool) -> str | None:
        found: str | None = self._read(scope)["scopes"].get(self._key(scope, shared))
        return found

    def created(self, scope: Scope) -> set[str]:
        return set(self._read(scope)["created"])

    def choose(self, scope: Scope, shared: bool, udid: str) -> None:
        data = self._read(scope)
        data["scopes"][self._key(scope, shared)] = udid
        self._write(scope, data)

    def forget(self, scope: Scope, shared: bool) -> str | None:
        data = self._read(scope)
        udid: str | None = data["scopes"].pop(self._key(scope, shared), None)
        if udid is not None:
            self._write(scope, data)
        return udid

    def remember_created(self, scope: Scope, shared: bool, udid: str) -> None:
        data = self._read(scope)
        data["scopes"][self._key(scope, shared)] = udid
        data["created"] = sorted({*data["created"], udid})
        self._write(scope, data)


class DeviceDirectory:
    """Every scope's device."""

    def __init__(self, memory: DeviceMemory, copy: HostCopy | None = None) -> None:
        self.memory = memory
        self._copy = copy or HostCopy()

    async def resolve(self, simctl: Simctl, scope: Scope, config: SimConfig) -> DeviceRef:
        """The scope's device: the remembered one while it exists, else a new one, remembered."""
        shared = config.device_mode == "shared"
        remembered = self.memory.assigned(scope, shared)
        if remembered:
            device = next((d for d in await simctl.devices() if d.udid == remembered and d.available), None)
            if device is not None:
                created = device.udid in self.memory.created(scope)
                return DeviceRef(device.udid, device.name, runtime_label(device.runtime_id), created=created)
        runtime = pick_runtime(await simctl.runtimes(), config.runtime)
        if runtime is None:
            raise NoDevice(self._copy.no_runtime(config.runtime))
        kind = pick_device_type(runtime, config.device_type)
        if kind is None:
            raise NoDevice(
                f"{runtime.name} has no device type named {config.device_type!r}."
                if config.device_type
                else f"{runtime.name} runs no iPhone or iPad."
            )
        name = device_name(config.device_name_prefix, scope, shared)
        udid = await simctl.create(name, kind.identifier, runtime.identifier)
        self.memory.remember_created(scope, shared, udid)
        return DeviceRef(udid, name, runtime.name, created=True)
