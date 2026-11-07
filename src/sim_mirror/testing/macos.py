# SPDX-License-Identifier: Apache-2.0
"""macOS process metadata and lifecycle, played without opening or signalling a real application."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from sim_mirror.platform.macos import MacApp, MacOS
from sim_mirror.testing.fakes import FakeEngine


class FakeMacOS:
    def __init__(self, *apps: MacApp) -> None:
        self.apps = list(apps)
        self.calls: list[tuple[str, ...]] = []
        self.signals: list[tuple[int, int]] = []
        self.fail: set[str] = set()
        self.starts = True
        self.stops = True
        self.log = "Timestamp Message\napp message\n"
        #: When each running PID was launched, as LaunchServices writes it; a PID not here lists no launch time.
        self.launched: dict[int, str] = {}
        #: The visible running PIDs, front first, as ``lsappinfo visibleProcessList`` answers.
        self.visible: list[int] = []
        self.mac = MacOS(self.run, signal_pid=self.signal_pid)

    async def run(self, argv: Sequence[str]) -> tuple[int, str]:
        self.calls.append(tuple(argv))
        if argv[0] in self.fail or " ".join(argv[:2]) in self.fail:
            return 1, ""
        if tuple(argv[:2]) == ("lsappinfo", "visibleProcessList"):
            return 0, " ".join(f'ASN:0x0-{pid:#x}-"App":' for pid in self.visible)
        if argv[0] == "lsappinfo":
            return 0, "\n".join(
                f'{i}) "{app.name}" ASN:0x0-{app.pid:#x}: \n bundleID="{app.bundle_id}"\n bundle path="{app.path}"\n'
                f' pid = {app.pid} type="Foreground"'
                + (f"\n launch time =  {self.launched[app.pid]} ( 1 minute ago )" if app.pid in self.launched else "")
                for i, app in enumerate(self.apps, 1)
                if app.pid is not None
            )
        if argv[0] == "open":
            if self.starts:
                path = argv[argv.index("-a") + 1]
                app = next(a for a in self.mac.known.values() if a.path == path)
                self.apps = [a for a in self.apps if a.path != path] + [replace(app, pid=777)]
            return 0, ""
        if argv[0] == "log":
            return 0, self.log
        raise AssertionError(f"unexpected fake macOS command: {argv[0]}")

    def signal_pid(self, pid: int, sig: int) -> None:
        self.signals.append((pid, sig))
        if not any(app.pid == pid for app in self.apps):
            raise ProcessLookupError
        if self.stops:
            self.apps = [replace(app, pid=None) if app.pid == pid else app for app in self.apps]


class FakeMacApp(FakeEngine):
    """Semantic app input, recorded without an Accessibility or WindowServer call."""

    def __init__(self, *, gestures: bool = False) -> None:
        super().__init__()
        self.mac_actions: list[dict[str, Any]] = []
        #: Whether this app's adapter holds clicks and drags in process, as a debug bridge's does.
        self.gestures = gestures
        #: What the app answers each command with.
        self.answer: dict[str, Any] = {}

    async def mac_action(self, action: dict[str, Any]) -> dict[str, Any]:
        self.mac_actions.append(dict(action))
        return dict(self.answer)


# Window frames use the same deterministic JPEG/H.264 source as other fake devices.
FakeWindowCapture = FakeEngine
