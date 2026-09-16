# SPDX-License-Identifier: Apache-2.0
"""A device's screen as Xcode 27 reads it: its UI hierarchy, through mcpbridge, as a `ScreenReader`.

Reading takes a device-interaction session in Xcode's tools, opened on the first read and kept while reads keep
coming -- opening one was measured at 0.05 to 1.8 seconds, a capture in it at 0.2 to 0.9. A device can be in only one
session at a time, so this one is ended `IDLE_S` after the last read, and when the device is let go (`close`): an agent
working in Xcode itself can then use the device, and the next read opens a session again. Each
read is a capture with no command: Xcode writes the hierarchy, a screenshot, a thumbnail and a log to files and answers
where they are. The hierarchy is read (`hierarchy.document_from_hierarchy`) and all four files are removed again, so a
long session does not leave a screenshot behind for every snapshot. Only files in the folder the hierarchy was written
to, named for this session, are ever removed.

What Xcode refuses is said so a person can act on it:

* **not approved** -- Xcode lets an agent use its tools once the agent has opened a project through them, which asks
  the person when Xcode is set to ask; the reason says how;
* **a session that ended** -- Xcode closed it, or another reader took its name -- is opened again once;
* **a device already in a session** -- Xcode lets a device have one at a time, measured -- is taken back when the
  session is one SimMirror left there, named for this device (a read that timed out, a process that died), since
  SimMirror's claim on the device means no other SimMirror is using it; another agent's session is said and left be;
* **an Xcode before 27** -- found once, before the first read -- says it needs Xcode 27.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import secrets
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

from sim_mirror.connectors.base import ConnectorError
from sim_mirror.connectors.mcpbridge.client import BridgeClient, BridgeError, BridgeRefused, find_bridge
from sim_mirror.connectors.mcpbridge.hierarchy import document_from_hierarchy
from sim_mirror.host_copy import HostCopy

START = "DeviceInteractionStartSession"
CAPTURE = "DeviceInteractionSynthesize"
END = "DeviceInteractionEndSession"
START_TIMEOUT_S = 60.0
CAPTURE_TIMEOUT_S = 30.0
END_TIMEOUT_S = 5.0
#: How long a session is kept without a read. Agents read in bursts -- a snapshot, steps, the diff after them.
IDLE_S = 60.0
#: The largest hierarchy read; a screen's is 4 to 20 KB.
HIERARCHY_MAX_BYTES = 8 * 1024 * 1024
#: The files a capture writes, by the key its answer names each under.
ARTIFACTS = ("hierarchyPath", "screenshotPath", "thumbnailScreenshotPath", "logsPath")
HIERARCHY_SUFFIX = "-hierarchy.txt"
#: What Xcode says, in part, when an agent has not been approved yet.
NOT_APPROVED = "isn't approved"
#: What Xcode says, in part, of a session that is gone or whose name was taken.
SESSION_GONE = ("Session not found", "currently in use or was recently used")
#: What xcrun says, in part, when the Xcode in use has no mcpbridge.
NO_BRIDGE = "unable to find utility"
#: What Xcode says of a device another session has, and that session's key.
IN_USE = re.compile(r"already in use by a different session with key '([^']*)'")

#: Where an Xcode keeps an mcpbridge SimMirror can use, or None.
FindBridge = Callable[[str], Awaitable[str | None]]


class _SessionGone(Exception):
    pass


class _DeviceInUse(ConnectorError):
    def __init__(self, message: str, key: str) -> None:
        super().__init__(message)
        self.key = key


def _read(path: Path) -> str:
    with path.open("rb") as file:
        return file.read(HIERARCHY_MAX_BYTES).decode("utf-8", errors="replace")


def _remove(paths: list[Path]) -> None:
    for path in paths:
        with contextlib.suppress(OSError):
            path.unlink()


class BridgeReader:
    """What is on one device's screen, as Xcode's UI hierarchy says, in the document shape idb_companion answers."""

    def __init__(
        self,
        udid: str,
        developer_dir: str = "",
        *,
        copy: HostCopy | None = None,
        client: BridgeClient | None = None,
        find: FindBridge = find_bridge,
        token: Callable[[], str] = lambda: secrets.token_hex(4),
        idle_s: float = IDLE_S,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.udid = udid
        self.developer_dir = developer_dir
        self._copy = copy or HostCopy()
        self._client = client or BridgeClient(developer_dir, client_name=self._copy.owner_name)
        self._find = find
        self._found = False
        self._token = token
        self._key: str | None = None
        #: How this reader's sessions are named: the host, then the device, then a token for each session.
        self._prefix = f"{self._copy.owner_name} {udid[:8]} "
        self._lock = asyncio.Lock()
        self._idle_s = idle_s
        self._sleep = sleep
        self._idle: asyncio.Task[None] | None = None

    async def accessibility(self) -> dict[str, Any]:
        self._stop_waiting()
        try:
            async with self._lock:
                try:
                    return await self._capture()
                except _SessionGone:
                    self._key = None
                try:
                    return await self._capture()
                except _SessionGone as exc:
                    raise ConnectorError(f"Xcode could not keep a session on this device: {exc}") from None
        finally:
            self._idle = asyncio.get_running_loop().create_task(self._end_when_idle())

    async def close(self) -> None:
        """End the session and the bridge. Xcode's session is ended if it can be; the bridge always is."""
        self._stop_waiting()
        await self._end()

    def _stop_waiting(self) -> None:
        if self._idle is not None:
            self._idle.cancel()
            self._idle = None

    async def _end_when_idle(self) -> None:
        await self._sleep(self._idle_s)
        # Shielded: a read that comes while the session is being ended waits for it, rather than cutting it short.
        await asyncio.shield(self._end())

    async def _end(self) -> None:
        async with self._lock:
            key, self._key = self._key, None
            if key is not None and self._client.alive:
                with contextlib.suppress(BridgeError):
                    await self._client.call(END, {"interactionSessionKey": key}, timeout=END_TIMEOUT_S)
            await self._client.close()

    async def _capture(self) -> dict[str, Any]:
        key = self._key or await self._start()
        answer = await self._call(CAPTURE, {"interactSessionKey": key}, CAPTURE_TIMEOUT_S)
        hierarchy, artifacts = self._artifacts(answer, key)
        if hierarchy is None:
            raise ConnectorError("Xcode captured the screen but named no hierarchy it wrote; read it again")
        try:
            text = await asyncio.to_thread(_read, hierarchy)
        except OSError as exc:
            raise ConnectorError(f"the hierarchy Xcode wrote could not be read: {exc.strerror or exc}") from None
        finally:
            await asyncio.to_thread(_remove, artifacts)
        return document_from_hierarchy(text)

    async def _start(self) -> str:
        if not self._found:
            if await self._find(self.developer_dir) is None:
                raise ConnectorError(self._copy.mcpbridge_missing(self.developer_dir))
            self._found = True
        try:
            answer = await self._open()
        except _DeviceInUse as busy:
            if not busy.key.startswith(self._prefix):
                raise
            with contextlib.suppress(BridgeError):
                await self._client.call(END, {"interactionSessionKey": busy.key}, timeout=END_TIMEOUT_S)
            answer = await self._open()
        key = answer.get("interactionSessionKey")
        if not isinstance(key, str) or not key:
            raise ConnectorError("Xcode opened no session on this device")
        self._key = key
        return key

    async def _open(self) -> dict[str, Any]:
        arguments = {"deviceIdentifier": self.udid, "sessionIdentifier": f"{self._prefix}{self._token()}"}
        return await self._call(START, arguments, START_TIMEOUT_S)

    async def _call(self, tool: str, arguments: Mapping[str, Any], timeout: float) -> dict[str, Any]:
        try:
            return await self._client.call(tool, arguments, timeout=timeout)
        except BridgeRefused as exc:
            if NOT_APPROVED in exc.text:
                raise ConnectorError(self._copy.xcode_not_approved()) from None
            if any(gone in exc.text for gone in SESSION_GONE):
                raise _SessionGone(exc.text) from None
            if busy := IN_USE.search(exc.text):
                raise _DeviceInUse(self._copy.xcode_device_in_use(busy[1]), busy[1]) from None
            raise ConnectorError(f"Xcode could not read the screen: {exc.text}") from None
        except BridgeError as exc:
            if NO_BRIDGE in str(exc):
                raise ConnectorError(self._copy.mcpbridge_missing(self.developer_dir)) from None
            raise

    @staticmethod
    def _artifacts(answer: Mapping[str, Any], key: str) -> tuple[Path | None, list[Path]]:
        """The hierarchy file a capture wrote, and every file of this capture that may be removed.

        Each must be in the folder the hierarchy is in, named for this session: a path Xcode answers is otherwise not
        SimMirror's to remove.
        """
        named = answer.get("hierarchyPath")
        hierarchy = Path(named) if isinstance(named, str) and named.endswith(HIERARCHY_SUFFIX) else None
        if hierarchy is None or not hierarchy.is_absolute() or not hierarchy.name.startswith(f"{key}-"):
            return None, []
        paths = [Path(value) for field in ARTIFACTS if isinstance(value := answer.get(field), str)]
        ours = [path for path in paths if path.parent == hierarchy.parent and path.name.startswith(f"{key}-")]
        return hierarchy, ours
