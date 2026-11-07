# SPDX-License-Identifier: Apache-2.0
"""LaunchServices and logs for one app; no AppleScript, clipboard or global input is used.

Only this module starts macOS command-line programs. Listings are process metadata, not accessibility trees.

A bundle ID is one device, however many copies of the app are installed: its identifier stays the same across
rebuilds and install folders. The device follows the copy that runs -- the one a device session is attached to while
it lives, else the frontmost visible copy, else the one launched last -- and remembers that copy's exact bundle path,
so a launch with no copy running opens the copy last seen running or installed, never another one.
"""

from __future__ import annotations

import asyncio
import os
import plistlib
import re
import signal
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from sim_mirror.platform import process
from sim_mirror.platform.errors import DeviceControlError
from sim_mirror.platform.identifiers import mac_id
from sim_mirror.platform.process import Runner


@dataclass(frozen=True)
class MacApp:
    bundle_id: str
    name: str
    path: str
    pid: int | None = None
    version: str = ""

    @property
    def id(self) -> str:
        return mac_id(self.bundle_id)


def bundle(path: str) -> MacApp:
    root = Path(path).resolve()
    try:
        info_path = root / "Contents/Info.plist"
        info = plistlib.loads((info_path if info_path.exists() else root / "Info.plist").read_bytes())
        identifier = info["CFBundleIdentifier"]
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9.-]{1,155}", identifier):
            raise ValueError("invalid bundle identifier")
        return MacApp(
            identifier,
            str(info.get("CFBundleDisplayName") or info.get("CFBundleName") or root.stem),
            str(root),
            version=str(info.get("CFBundleShortVersionString", "")),
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise DeviceControlError("use a macOS .app with a readable Contents/Info.plist") from exc


@dataclass(frozen=True)
class Running:
    """One running copy as LaunchServices lists it, with what ranks it among copies that share its bundle ID."""

    app: MacApp
    #: LaunchServices' serial number for this launch, which its visible process list names.
    asn: str = ""
    #: When it was launched -- or checked in, for an app launchd started -- as ``YYYY/MM/DD HH:MM:SS``, text that sorts
    #: as time does; empty when unlisted.
    launched: str = ""
    #: Where LaunchServices listed it, which breaks a tie between two launches in one second.
    order: int = 0


def applications(text: str) -> list[Running]:
    found = []
    for order, entry in enumerate(re.split(r"(?m)^\s*\d+\) ", text)[1:]):
        identifier = re.search(r'(?m)^\s*bundleID="([A-Za-z0-9.-]+)"', entry)
        path = re.search(r'(?m)^\s*bundle path="([^"\n]+\.app)"', entry)
        pid = re.search(r'pid = (\d+)\b.*?type="(?:Foreground|UIElement)"', entry)
        name = re.match(r'"([^"\n]+)"', entry)
        if identifier and path and pid and name:
            version = re.search(r'\bVersion="([^"\n]*)"', entry)
            asn = re.match(r'"[^"\n]+"\s+ASN:(0x[0-9A-Fa-f]+-0x[0-9A-Fa-f]+)', entry)
            launched = re.search(r"(?m)^\s*(?:launch|checkin) time =\s+(\d{4}/\d\d/\d\d \d\d:\d\d:\d\d)", entry)
            app = MacApp(identifier[1], name[1], path[1], int(pid[1]), version[1] if version else "")
            found.append(Running(app, asn[1] if asn else "", launched[1] if launched else "", order))
    return found


def front_to_back(text: str) -> dict[str, int]:
    """Each visible app's place, front first, from ``lsappinfo visibleProcessList``; a hidden app is not listed."""
    return {asn: place for place, asn in enumerate(re.findall(r'ASN:(0x[0-9A-Fa-f]+-0x[0-9A-Fa-f]+)-"', text))}


class MacOS:
    def __init__(self, run: Runner = process.run, *, signal_pid: Callable[[int, int], None] = os.kill) -> None:
        self.run = run
        self.signal_pid = signal_pid
        self.known: dict[str, MacApp] = {}
        self.started: dict[str, int] = {}
        self.windows: dict[str, int] = {}
        #: The process a device session is attached to, which the device keeps following while it runs.
        self.held: dict[str, int] = {}

    async def apps(self) -> list[MacApp]:
        code, out = await self.run(("lsappinfo", "list"))
        if code:
            raise DeviceControlError("LaunchServices could not list running macOS apps")
        copies: dict[str, list[Running]] = {}
        for copy in applications(out):
            copies.setdefault(copy.app.id, []).append(copy)
        # Only a bundle ID with several running copies needs to know which is in front.
        front = await self._front() if any(len(found) > 1 for found in copies.values()) else {}
        for udid in copies:
            self.known.setdefault(udid, copies[udid][0].app)
        for udid, before in self.known.items():
            found = copies.get(udid)
            now = self._follow(udid, found, front) if found else replace(before, pid=None)
            if now.path == before.path and not now.version:
                now = replace(now, version=before.version)
            if now.pid != before.pid:
                self.windows.pop(udid, None)
            self.known[udid] = now
        return list(self.known.values())

    def _follow(self, udid: str, found: Sequence[Running], front: dict[str, int]) -> MacApp:
        """The running copy a device follows: the one its session holds, else the frontmost, else the newest."""
        held = self.held.get(udid)
        return max(found, key=lambda c: (c.app.pid == held, -front.get(c.asn, len(front)), c.launched, c.order)).app

    async def _front(self) -> dict[str, int]:
        code, out = await self.run(("lsappinfo", "visibleProcessList"))
        # Without the visible list the newest launch still decides, so a failure here costs nothing more.
        return {} if code else front_to_back(out)

    async def app(self, udid: str) -> MacApp:
        app = next((a for a in await self.apps() if a.id == udid), None)
        if app is None:
            raise DeviceControlError("that macOS app is not known; open it once, then choose it")
        return app

    async def launch(self, udid: str, args: Sequence[str] = ()) -> int:
        app = await self.app(udid)
        if app.pid is not None:
            return app.pid
        code, _ = await self.run(("open", "-g", "-n", "-a", app.path, "--args", *args))
        if code:
            raise DeviceControlError("LaunchServices could not launch this app in the background")
        for _ in range(100):
            running = await self.app(udid)
            if running.pid is not None:
                self.started[udid] = running.pid
                return running.pid
            await asyncio.sleep(0.05)
        raise DeviceControlError("the macOS app did not appear in LaunchServices after launch")

    async def terminate(self, udid: str) -> bool:
        app = await self.app(udid)
        if app.pid is None:
            return False
        try:
            self.signal_pid(app.pid, signal.SIGTERM)
        except ProcessLookupError:
            return False
        for _ in range(100):
            if (await self.app(udid)).pid != app.pid:
                self.started.pop(udid, None)
                return True
            await asyncio.sleep(0.05)
        raise DeviceControlError("the app did not quit; no other process was signalled")

    async def logs(self, udid: str, since_s: int) -> list[str]:
        app = await self.app(udid)
        if app.pid is None:
            return []
        code, out = await self.run(
            (
                "log",
                "show",
                "--style",
                "compact",
                "--last",
                f"{since_s}s",
                "--predicate",
                f"processIdentifier == {app.pid}",
            )
        )
        if code:
            raise DeviceControlError("the app's log could not be read")
        return out.splitlines()[1:]


LAYOUT_QUERY = ("defaults", "read", "com.apple.HIToolbox", "AppleCurrentKeyboardLayoutInputSourceID")


async def keyboard_layout(run: Runner = process.run) -> tuple[int, str]:
    """Read the active keyboard preference; this module is the sole owner of defaults."""
    return await run(LAYOUT_QUERY)
