# SPDX-License-Identifier: Apache-2.0
"""LaunchServices and logs for one app; no AppleScript, clipboard or global input is used.

Only this module starts macOS command-line programs. Listings are process metadata, not accessibility trees.
Applications already running are remembered by their exact bundle path so another installed copy cannot win a launch.
"""

from __future__ import annotations

import asyncio
import os
import plistlib
import re
import signal
from collections.abc import Callable, Sequence
from dataclasses import dataclass
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


def applications(text: str) -> list[MacApp]:
    found = []
    for entry in re.split(r"(?m)^\s*\d+\) ", text)[1:]:
        identifier = re.search(r'(?m)^\s*bundleID="([A-Za-z0-9.-]+)"', entry)
        path = re.search(r'(?m)^\s*bundle path="([^"\n]+\.app)"', entry)
        pid = re.search(r'pid = (\d+)\b.*?type="(?:Foreground|UIElement)"', entry)
        name = re.match(r'"([^"\n]+)"', entry)
        if identifier and path and pid and name:
            found.append(MacApp(identifier[1], name[1], path[1], int(pid[1])))
    return found


class MacOS:
    def __init__(self, run: Runner = process.run, *, signal_pid: Callable[[int, int], None] = os.kill) -> None:
        self.run = run
        self.signal_pid = signal_pid
        self.known: dict[str, MacApp] = {}
        self.started: dict[str, int] = {}
        self.windows: dict[str, int] = {}

    async def apps(self) -> list[MacApp]:
        code, out = await self.run(("lsappinfo", "list"))
        if code:
            raise DeviceControlError("LaunchServices could not list running macOS apps")
        running = applications(out)
        for app in running:
            self.known.setdefault(app.id, app)
        pids = {(app.id, app.path): app.pid for app in running}
        return [MacApp(a.bundle_id, a.name, a.path, pids.get((a.id, a.path)), a.version) for a in self.known.values()]

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
