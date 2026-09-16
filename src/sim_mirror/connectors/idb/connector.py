# SPDX-License-Identifier: Apache-2.0
"""The idb connector: full control of a device through idb_companion.

It can be used wherever idb_companion is installed (`companion.find_companion`), and does everything a connector can:
the screen as JPEG or H.264, touches, buttons, keys and text, and the accessibility tree. Attaching starts a
companion for the device, told which Xcode to run with (`platform.developer_dir.choose_xcode`); closing the session
ends it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import Capability, ConnectorReport, ConnectorUnavailable, DeviceSession
from sim_mirror.connectors.idb.companion import CompanionLauncher, find_companion
from sim_mirror.connectors.registry import ConnectorContext
from sim_mirror.host_copy import HostCopy
from sim_mirror.platform.developer_dir import ChosenXcode, choose_xcode

NAME = "idb"
#: Everything a device can be asked for; building is SimMirror's own, not a connector's.
CAPABILITIES = frozenset(Capability) - {Capability.BUILD_PREVIEW}

#: Which Xcode a companion started now runs with, given the scope's ``device.developer_dir``.
ChooseXcode = Callable[[str], Awaitable[ChosenXcode | None]]


class IdbConnector:
    name = NAME

    def __init__(
        self,
        launcher: CompanionLauncher,
        *,
        copy: HostCopy | None = None,
        find: Callable[[str], str | None] = find_companion,
        choose: ChooseXcode = choose_xcode,
    ) -> None:
        self._launcher = launcher
        self._copy = copy or HostCopy()
        self._find = find
        self._choose = choose

    async def probe(self, config: SimConfig) -> ConnectorReport:
        binary = self._find(config.companion_path)
        if binary is None:
            return ConnectorReport(NAME, False, reasons=(self._copy.companion_missing(config.companion_path),))
        return ConnectorReport(NAME, True, CAPABILITIES, {"idb_companion": binary})

    async def attach(self, udid: str, config: SimConfig) -> DeviceSession:
        binary = self._find(config.companion_path)
        if binary is None:
            raise ConnectorUnavailable(self._copy.companion_missing(config.companion_path), 409)
        # Named explicitly even when the setting is empty: the Xcode xcode-select names when this companion starts is
        # the one it keeps, and the pid file then says which that was.
        xcode = await self._choose(config.developer_dir)
        companion = await self._launcher.start(binary, udid, xcode.path if xcode else "")

        async def close() -> None:
            await self._launcher.stop(companion)

        def alive() -> bool:
            return companion.alive

        engine = companion.engine
        return DeviceSession(
            connector=NAME,
            capabilities=CAPABILITIES,
            screen=engine,
            input=engine,
            reader=engine,
            is_alive=alive,
            on_close=close,
        )

    async def reap_orphans(self) -> int:
        return await self._launcher.reap_orphans()


def create(context: ConnectorContext) -> IdbConnector:
    """The idb connector for a host: its companions' sockets, pid files and logs in the host's folders."""
    state = context.state
    launcher = CompanionLauncher(
        run_dir=state.run_dir(),
        log_dir=state.log_dir(),
        owner_tag=state.owner_tag,
        copy=context.copy,
        ensure_dir=state.ensure_dir,
    )
    return IdbConnector(launcher, copy=context.copy)
