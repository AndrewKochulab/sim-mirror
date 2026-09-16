# SPDX-License-Identifier: Apache-2.0
"""The mcpbridge connector: a device's screen, and what is on it, with nothing but Xcode 27.

It shows the screen as the simctl connector does -- JPEG, a few frames a second, and everything simctl does -- and
reads it through Xcode's UI hierarchy (`reader.BridgeReader`), so an agent can take snapshots on a Mac without
idb_companion. It does not touch the device: measured on Xcode 27.0, a tap through Xcode's tools answers after 3.3
seconds and a button press after 5.7, since each waits for the screen to settle and captures it, which is too slow for
a person's hand or an agent's steps. ``auto`` never chooses it; ``connectors.preferred = "mcpbridge"`` does.

Where another connector drives the device, `merge.HierarchyMerge` adds Xcode's hierarchy to its snapshots instead.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import Capability, ConnectorReport, ConnectorUnavailable, DeviceSession
from sim_mirror.connectors.mcpbridge.client import find_bridge
from sim_mirror.connectors.mcpbridge.reader import BridgeReader
from sim_mirror.connectors.registry import ConnectorContext
from sim_mirror.connectors.simctl import connector as simctl
from sim_mirror.connectors.simctl.capture import SimctlScreen
from sim_mirror.host_copy import HostCopy
from sim_mirror.platform.simctl import Simctl

NAME = "mcpbridge"
CAPABILITIES = simctl.CAPABILITIES | {Capability.ELEMENT_TREE}

#: Where the scope's Xcode keeps mcpbridge, or None.
FindBridge = Callable[[str], Awaitable[str | None]]
ReaderFor = Callable[[str, str], BridgeReader]


class McpBridgeConnector:
    name = NAME

    def __init__(
        self,
        simctl_for: Callable[[str], Simctl],
        *,
        copy: HostCopy | None = None,
        find: FindBridge = find_bridge,
        reader_for: ReaderFor | None = None,
        screen_for: Callable[[Simctl, str], SimctlScreen] = SimctlScreen,
    ) -> None:
        self._simctl_for = simctl_for
        self._copy = copy or HostCopy()
        self._find = find
        self._reader_for = reader_for or self._reader
        self._screen_for = screen_for

    def _reader(self, udid: str, developer_dir: str) -> BridgeReader:
        return BridgeReader(udid, developer_dir, copy=self._copy)

    async def probe(self, config: SimConfig) -> ConnectorReport:
        bridge = await self._find(config.developer_dir)
        if bridge is None:
            return ConnectorReport(NAME, False, reasons=(self._copy.mcpbridge_missing(config.developer_dir),))
        return ConnectorReport(NAME, True, CAPABILITIES, {"mcpbridge": bridge})

    async def attach(self, udid: str, config: SimConfig) -> DeviceSession:
        if await self._find(config.developer_dir) is None:
            raise ConnectorUnavailable(self._copy.mcpbridge_missing(config.developer_dir), 409)
        reader = self._reader_for(udid, config.developer_dir)
        return DeviceSession(
            connector=NAME,
            capabilities=CAPABILITIES,
            screen=self._screen_for(self._simctl_for(config.developer_dir), udid),
            reader=reader,
            fps_limit=simctl.FPS_LIMIT,
            on_close=reader.close,
        )

    async def reap_orphans(self) -> int:
        # A bridge is this process's child and ends when its stdin closes, which a process that died closes too.
        return 0


def create(context: ConnectorContext) -> McpBridgeConnector:
    async def find(developer_dir: str) -> str | None:
        return await find_bridge(developer_dir, context.xcrun)

    return McpBridgeConnector(context.simctl_for, copy=context.copy, find=find)
