# SPDX-License-Identifier: Apache-2.0
"""A real `DeviceManager` over a fake Mac, for SimMirror's tests and a host's.

simctl lists the fixture's devices and -- like a real one -- those it creates; the connectors are fakes over a
`FakeEngine` (`idb` with full control, `simctl` view-only, and a `native` one before them when a test gives it);
settings, state and policy are in memory; the clock is a hand-moved one. Nothing boots, nothing is spawned, and every
seam can be changed by a test.
"""

from __future__ import annotations

import asyncio
import itertools
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from sim_mirror.connectors.base import Capability
from sim_mirror.connectors.registry import ConnectorRegistry
from sim_mirror.core.availability import Availability
from sim_mirror.core.devices import DeviceDirectory, JsonDeviceMemory
from sim_mirror.core.instance import DeviceInstance
from sim_mirror.core.manager import DeviceManager
from sim_mirror.host_copy import HostCopy
from sim_mirror.platform.simctl import Simctl
from sim_mirror.platform.xcrun import XcrunResult
from sim_mirror.scope import Scope
from sim_mirror.storage.claims import Claims
from sim_mirror.testing.fakes import (
    FakeConnector,
    FakeKeyboard,
    FakePolicy,
    FakeXcrun,
    ManualClock,
    MemoryStateStore,
    StaticConfig,
    fixture_json,
    made,
    no_wait,
)

#: What the simctl connector can do: show the screen and manage the device, but not touch or read it.
VIEW_ONLY = frozenset(
    {
        Capability.LIFECYCLE,
        Capability.DEVICE_LIST,
        Capability.APPEARANCE,
        Capability.OPEN_URL,
        Capability.APP_INSTALL,
        Capability.APP_LAUNCH,
        Capability.LOGS,
        Capability.SCREENSHOT,
        Capability.STREAM_JPEG,
    }
)
GROUP = "alpha"
OWNER_PID = 4000


def scope(scope_id: str = "tp-1", group: str = GROUP) -> Scope:
    return Scope(id=scope_id, group=group, label=f"{group} · {scope_id}")


class DeviceRig:
    def __init__(
        self,
        root: Path,
        *,
        idb: FakeConnector | None = None,
        simctl: FakeConnector | None = None,
        native: FakeConnector | None = None,
        platform: str = "darwin",
        list_created: bool = True,
        sleep: Callable[[float], Awaitable[None]] = no_wait,
        copy: HostCopy | None = None,
        keyboard: FakeKeyboard | None = None,
    ) -> None:
        self.root = root
        #: The Mac's keyboard layout: not US-shaped unless a test says so, so text is pasted as it always was.
        self.keyboard = keyboard or FakeKeyboard()
        self.config = StaticConfig(max_booted=8)
        self.state = MemoryStateStore(root)
        #: The `DeviceMemory` a test builds a runtime with -- `Runtime.build` asks for one rather than assuming a file.
        self.memory = JsonDeviceMemory(self.state.devices_file())
        self.policy = FakePolicy()
        self.copy = copy or HostCopy()
        self.clock = ManualClock()
        self.devices: dict[str, Any] = fixture_json("simctl-devices.json")
        counter = itertools.count(1)

        def create(args: tuple[str, ...]) -> XcrunResult:
            udid = made(next(counter))
            if list_created:
                self.devices["devices"].setdefault(args[4], []).append(
                    {
                        "udid": udid,
                        "name": args[2],
                        "state": "Shutdown",
                        "isAvailable": True,
                        "deviceTypeIdentifier": args[3],
                    }
                )
            return XcrunResult(0, udid + "\n", "")

        self.xcrun = (
            FakeXcrun()
            .with_lists()
            .on("simctl", "list", "devices", "-j", then=lambda args: XcrunResult(0, json.dumps(self.devices), ""))
            .on("simctl", "create", then=create)
        )
        self.idb = idb or FakeConnector("idb")
        self.simctl_connector = simctl or FakeConnector("simctl", capabilities=VIEW_ONLY, fps_limit=4)
        self.native = native
        connectors = [self.idb, self.simctl_connector] if native is None else [native, self.idb, self.simctl_connector]
        self.registry = ConnectorRegistry(connectors, copy=self.copy)
        self.alive_pids: set[int] = {OWNER_PID}
        self.claims = Claims(
            root / "claims",
            owner="SimMirrorTest",
            pid=OWNER_PID,
            pid_alive=lambda pid: pid in self.alive_pids,
            start_time=self._start_time,
        )
        self.availability = Availability(
            config=self.config, policy=self.policy, registry=self.registry, copy=self.copy, platform=platform
        )
        self.manager = DeviceManager(
            config=self.config,
            availability=self.availability,
            directory=DeviceDirectory(self.memory, self.copy),
            claims=self.claims,
            simctl_for=lambda developer_dir: Simctl(self.xcrun, developer_dir=developer_dir),
            copy=self.copy,
            keyboard_is_us=self.keyboard,
            clock=self.clock,
            sleep=sleep,
        )

    @staticmethod
    async def _start_time(pid: int) -> str | None:
        return f"started-{pid}"

    async def up(self, scope_id: str = "tp-1", group: str = GROUP) -> DeviceInstance:
        instance = await self.manager.ensure(scope(scope_id, group))
        assert instance.task is not None
        await instance.task
        return instance

    def argv(self) -> list[tuple[str, ...]]:
        return self.xcrun.argv()


def closer_log() -> tuple[list[tuple[int, str]], Callable[[int, str], Awaitable[None]]]:
    """A screen socket's close, recorded."""
    closed: list[tuple[int, str]] = []

    async def close(code: int, reason: str) -> None:
        closed.append((code, reason))

    return closed, close


async def settle(tries: int = 50) -> None:
    """Let background tasks run for a while."""
    for _ in range(tries):
        await asyncio.sleep(0)
