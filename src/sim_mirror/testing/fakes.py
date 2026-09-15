# SPDX-License-Identifier: Apache-2.0
"""Fakes for everything SimMirror reaches outside itself, so no test runs xcrun, boots a device or starts a companion.

`FakeXcrun` stands where `platform.xcrun.run_xcrun` does. It records every call and answers from a script of argv
prefixes, the latest matching one winning, so a test says only what it cares about and every other call succeeds
quietly. What simctl, xcodebuild, xcresulttool and idb_companion really printed on a Mac is in `fixtures/`.

`StaticConfig` and `MemoryStateStore` are the in-memory `ConfigSource` and `StateStore` a test runs a core with.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sim_mirror.config.model import SimConfig
from sim_mirror.platform.xcrun import XcrunResult
from sim_mirror.scope import Scope
from sim_mirror.storage.private import ensure_private_dir

FIXTURES = Path(__file__).parent / "fixtures"

#: The booted iPhone 17 Pro in `simctl-devices.json`.
BOOTED_UDID = "D946616B-6E4F-4F5C-8C76-54FAD9B7D702"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fixture_json(name: str) -> Any:
    return json.loads(fixture(name))


def fixture_udid(name: str) -> str:
    """The device id of the device named `name` in `simctl-devices.json`."""
    devices = fixture_json("simctl-devices.json")["devices"]
    return str(next(device["udid"] for entries in devices.values() for device in entries if device["name"] == name))


def made(n: int) -> str:
    """The device id the fake Mac gives the n-th device it creates."""
    return f"11111111-2222-3333-4444-{n:012d}"


@dataclass(frozen=True)
class XcrunCall:
    args: tuple[str, ...]
    timeout: float
    developer_dir: str
    input_data: bytes | None
    cwd: str | None


Answer = XcrunResult | Callable[[tuple[str, ...]], XcrunResult]


class FakeXcrun:
    """Plays xcrun: records each call, answers from `on(...)`."""

    def __init__(self) -> None:
        self.calls: list[XcrunCall] = []
        self._answers: list[tuple[tuple[str, ...], Answer]] = []

    def on(
        self,
        *prefix: str,
        rc: int = 0,
        out: str = "",
        err: str = "",
        raw: bytes | None = None,
        then: Answer | None = None,
    ) -> FakeXcrun:
        answer = then if then is not None else XcrunResult(rc, out, err, out.encode() if raw is None else raw)
        self._answers.append((prefix, answer))
        return self

    def with_lists(self) -> FakeXcrun:
        """Answer `simctl list devices|runtimes -j` with what a real Mac printed."""
        return self.on("simctl", "list", "devices", "-j", out=fixture("simctl-devices.json")).on(
            "simctl", "list", "runtimes", "-j", out=fixture("simctl-runtimes.json")
        )

    async def __call__(
        self,
        *args: str,
        timeout: float = 30.0,
        developer_dir: str = "",
        input_data: bytes | None = None,
        cwd: Path | str | None = None,
    ) -> XcrunResult:
        self.calls.append(XcrunCall(tuple(args), timeout, developer_dir, input_data, None if cwd is None else str(cwd)))
        for prefix, answer in reversed(self._answers):
            if tuple(args[: len(prefix)]) == prefix:
                return answer(tuple(args)) if callable(answer) else answer
        return XcrunResult(0, "", "")

    def argv(self) -> list[tuple[str, ...]]:
        return [call.args for call in self.calls]


class FakeProcess:
    """A started program: runs until a test finishes it."""

    def __init__(self, pid: int = 4242) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self._done = asyncio.Event()

    def finish(self, rc: int = 0) -> None:
        self.returncode = rc
        self._done.set()

    async def wait(self) -> int | None:
        await self._done.wait()
        return self.returncode


class ManualClock:
    """A monotonic clock a test moves by hand."""

    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


async def no_wait(seconds: float) -> None:
    """A sleep that only lets other tasks run."""
    await asyncio.sleep(0)


class StaticConfig:
    """A `ConfigSource` a test sets directly: one config for every scope, or one per scope id."""

    def __init__(self, config: SimConfig | None = None, **values: Any) -> None:
        self.config = (config or SimConfig.defaults()).with_values(**values)
        self.scopes: dict[str, SimConfig] = {}

    def get(self, scope: Scope) -> SimConfig:
        return self.scopes.get(scope.id, self.config)

    def set(self, **values: Any) -> None:
        """Change the config every scope without its own gets."""
        self.config = self.config.with_values(**values)

    def set_for(self, scope_id: str, **values: Any) -> None:
        """Give one scope its own config: the shared one with these values changed."""
        self.scopes[scope_id] = self.scopes.get(scope_id, self.config).with_values(**values)


class MemoryStateStore:
    """A `StateStore` whose every folder is under one root a test owns."""

    def __init__(self, root: Path, *, owner_tag: str = "sim-mirror-test") -> None:
        self.root = root
        self._owner_tag = owner_tag

    @property
    def owner_tag(self) -> str:
        return self._owner_tag

    def devices_file(self, scope: Scope) -> Path:
        return self.root / "state" / "devices.json"

    def builds_dir(self, scope: Scope) -> Path:
        return self.root / "state" / "builds" / scope.id.replace(":", "_")

    def derived_data(self, scope: Scope) -> Path:
        return self.root / "DerivedData" / scope.id.replace(":", "_")

    def run_dir(self) -> Path:
        return self.root / "run"

    def log_dir(self) -> Path:
        return self.root / "logs"

    def claims_dir(self) -> Path:
        return self.root / "claims"

    def ensure_dir(self, folder: Path) -> Path:
        return ensure_private_dir(folder)
