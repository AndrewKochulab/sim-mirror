# SPDX-License-Identifier: Apache-2.0
"""The native connector: full control of a device through SimMirror's own helper, with nothing but Xcode.

It does everything the idb connector does -- the screen as JPEG or H.264, touches, buttons, keys and text, and the
accessibility tree -- without idb_companion, and faster: the helper reads the simulator's framebuffer as shared
memory, encodes the moment a frame is presented, and sends input straight to the device's HID service
(`connectors.native.helper`). ``auto`` tries it first.

Attaching starts a helper for the device, with the scope's Xcode, and greets it: a helper that starts but cannot send
input to the device -- the one thing only a helper that runs can tell -- is ended and refused, so ``auto`` goes on to
idb. The element tree's first read on a device is slow while the simulator's accessibility wakes up, so it is read once
as the session starts, in the background.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import Capability, ConnectorError, ConnectorReport, ConnectorUnavailable, DeviceSession
from sim_mirror.connectors.native.helper import (
    PACKAGED,
    PROGRAM,
    HelperLauncher,
    HelperVersion,
    built_helper,
    helper_version,
    locate_helper,
)
from sim_mirror.connectors.registry import ConnectorContext
from sim_mirror.host_copy import HostCopy
from sim_mirror.platform.developer_dir import ChosenXcode, choose_xcode
from sim_mirror.storage import app_support

NAME = "native"
#: Everything a device can be asked for; building is SimMirror's own, not a connector's.
CAPABILITIES = frozenset(Capability) - {Capability.BUILD_PREVIEW}

ChooseXcode = Callable[[str], Awaitable[ChosenXcode | None]]
AskVersion = Callable[[str], Awaitable[HelperVersion | None]]


def default_candidates() -> Sequence[Path]:
    """Where a helper is looked for when none is configured: shipped with SimMirror, then built for this version."""
    return (PACKAGED, built_helper(app_support.state_dir(os.environ)))


class NativeConnector:
    name = NAME

    def __init__(
        self,
        launcher: HelperLauncher,
        *,
        copy: HostCopy | None = None,
        candidates: Callable[[], Sequence[Path]] = default_candidates,
        ask_version: AskVersion = helper_version,
        choose: ChooseXcode = choose_xcode,
    ) -> None:
        self._launcher = launcher
        self._copy = copy or HostCopy()
        self._candidates = candidates
        self._ask_version = ask_version
        self._choose = choose
        #: What each helper said of its version, by path and modification time, so probing stays cheap.
        self._versions: dict[tuple[str, int], HelperVersion | None] = {}

    async def _version(self, binary: str) -> HelperVersion | None:
        """What a helper says of its version, asked once for each path and modification time."""
        key = (binary, _modified(binary))
        if key not in self._versions:
            self._versions[key] = await self._ask_version(binary)
        return self._versions[key]

    async def _usable(self, config: SimConfig) -> tuple[str, HelperVersion] | str:
        """The helper to run and its version, or why there is none."""
        found = await locate_helper(config.native_helper_path, self._candidates(), self._version)
        if found.binary is None or found.version is None or not found.usable:
            return found.reason(self._copy, config.native_helper_path) or ""
        return found.binary, found.version

    async def probe(self, config: SimConfig) -> ConnectorReport:
        found = await self._usable(config)
        if isinstance(found, str):
            return ConnectorReport(NAME, False, reasons=(found,))
        binary, version = found
        versions = {PROGRAM: binary, "version": version.version}
        if version.core_simulator:
            versions["CoreSimulator"] = version.core_simulator
        return ConnectorReport(NAME, True, CAPABILITIES, versions)

    async def attach(self, udid: str, config: SimConfig) -> DeviceSession:
        found = await self._usable(config)
        if isinstance(found, str):
            raise ConnectorUnavailable(found, 409)
        binary, _version = found
        xcode = await self._choose(config.developer_dir)
        running = await self._launcher.start(
            binary,
            udid,
            xcode.path if xcode else "",
            hid=config.native_hid_transport,
            idle_key_frames=config.native_idle_key_frames,
            ready_timeout_s=config.native_startup_timeout,
        )
        client = running.engine
        try:
            hello = await client.hello()
        except (ConnectorError, asyncio.CancelledError):
            await self._launcher.stop(running)
            raise
        if hello.hid is None:
            await self._launcher.stop(running)
            reasons = "; ".join(hello.reasons) or "it did not say why"
            raise ConnectorUnavailable(self._copy.helper_input_unreachable(reasons), 409)
        warming = asyncio.ensure_future(_warm(client.accessibility))

        async def close() -> None:
            warming.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await warming
            await self._launcher.stop(running)

        def alive() -> bool:
            return running.alive

        return DeviceSession(
            connector=NAME,
            capabilities=CAPABILITIES,
            screen=client,
            input=client,
            reader=client,
            is_alive=alive,
            on_close=close,
        )

    async def reap_orphans(self) -> int:
        return await self._launcher.reap_orphans()


async def _warm(read: Callable[[], Awaitable[object]]) -> None:
    """Read the screen once so the next read is quick; a read that fails now is no failure of the session."""
    with contextlib.suppress(ConnectorError):
        await read()


def _modified(path: str) -> int:
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return 0


def create(context: ConnectorContext) -> NativeConnector:
    """The native connector for a host: its helpers' sockets, pid files and logs in the host's folders."""
    state = context.state
    launcher = HelperLauncher(
        run_dir=state.run_dir(),
        log_dir=state.log_dir(),
        owner_tag=state.owner_tag,
        copy=context.copy,
        ensure_dir=state.ensure_dir,
    )
    return NativeConnector(launcher, copy=context.copy)
