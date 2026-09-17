# SPDX-License-Identifier: Apache-2.0
"""What each connector costs a person watching and an agent acting, measured on a booted simulator.

    uv run python benchmarks/connector_latency.py --device <UDID> --rounds 10

It attaches each connector (`--connectors`, native, idb and simctl by default) to the device in this process -- not
through the daemon, so what is measured is the connector's own cost -- and times, for each one that can:

* **attach**: starting it for the device until it describes the screen;
* **first frame**: asking for an H.264 stream until its first access unit arrives;
* **screenshots**: a 900-pixel JPEG, as a viewer's JPEG stream takes, and a 160-pixel one, as settle detection does;
* **snapshot**: reading the element tree, and how many elements it has;
* **input**: a tap's events sent until the connector says they went out;
* **tap to change**: a tap on Settings' General row until a screenshot differs -- what an agent waits for.

Settings is launched fresh before each connector and each tap, through simctl. The device is only tapped in Settings.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass, field
from typing import IO, Any

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import Capability, Connector, ConnectorError, DeviceSession, HidEvent, Screen
from sim_mirror.connectors.registry import ConnectorContext, ConnectorRegistry
from sim_mirror.host_copy import HostCopy
from sim_mirror.perception.model import ElementNode
from sim_mirror.perception.readers import tree_from_document
from sim_mirror.platform.simctl import Simctl, SimctlError
from sim_mirror.platform.xcrun import run_xcrun
from sim_mirror.storage.app_support import AppSupportStateStore
from tool_budget import percentile

SETTINGS_APP = "com.apple.Preferences"
DEFAULT_CONNECTORS = ("native", "idb", "simctl")
#: How long Settings is given to come up after it is launched.
LAUNCH_SETTLE_S = 1.5
#: How long Settings may take to show its first page after it is launched: iOS 27 takes over two seconds.
LAUNCH_WAIT_S = 5.0
FIRST_FRAME_TIMEOUT_S = 10.0
CHANGE_TIMEOUT_S = 5.0
CHANGE_POLL_S = 0.005
#: How long a tap is held, as `core.gestures` holds one.
TAP_HOLD_S = 0.05
#: How long the screen is given to stop changing before a tap whose effect is timed.
SETTLE_TIMEOUT_S = 3.0
#: A tap at the top middle of the screen, on the status bar: it changes nothing on Settings' first page.
INERT_Y_PT = 6.0


@dataclass(frozen=True)
class Timing:
    """Measured durations, in milliseconds."""

    p50: float
    p95: float
    runs: int

    @classmethod
    def of(cls, seconds: Sequence[float]) -> Timing | None:
        if not seconds:
            return None
        return cls(round(percentile(seconds, 50) * 1000, 1), round(percentile(seconds, 95) * 1000, 1), len(seconds))


@dataclass
class Result:
    connector: str
    attach_ms: float | None = None
    first_frame_ms: float | None = None
    screenshot_900: Timing | None = None
    screenshot_160: Timing | None = None
    snapshot: Timing | None = None
    snapshot_elements: int | None = None
    input: Timing | None = None
    tap_to_change: Timing | None = None
    #: Why the connector could not be measured, or measured only in part.
    notes: list[str] = field(default_factory=list)


Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]
Prepare = Callable[[], Awaitable[None]]


async def _tap(x: float, y: float, sleep: Sleep | None = None) -> AsyncIterator[HidEvent]:
    """A tap, held as a finger holds one when `sleep` is given."""
    yield HidEvent.touch("down", x, y)
    if sleep is not None:
        await sleep(TAP_HOLD_S)
    yield HidEvent.touch("up", x, y)


async def _timed(clock: Clock, call: Callable[[], Awaitable[object]], rounds: int) -> list[float]:
    seconds: list[float] = []
    for _ in range(rounds):
        started = clock()
        await call()
        seconds.append(clock() - started)
    return seconds


class Bench:
    """Measures one connector at a time on one device."""

    def __init__(self, udid: str, config: SimConfig, rounds: int, prepare: Prepare, clock: Clock, sleep: Sleep) -> None:
        self.udid = udid
        self.config = config
        self.rounds = rounds
        self.prepare = prepare
        self.clock = clock
        self.sleep = sleep

    async def run(self, connector: Connector) -> Result:
        result = Result(connector.name)
        report = await connector.probe(self.config)
        if not report.available:
            result.notes.append("not available here: " + " ".join(report.reasons))
            return result
        await self.prepare()
        started = self.clock()
        try:
            session = await connector.attach(self.udid, self.config)
        except ConnectorError as exc:
            result.notes.append(f"could not attach: {exc}")
            return result
        try:
            screen = await session.screen.describe()
            result.attach_ms = round((self.clock() - started) * 1000, 1)
            await self._measure(session, screen, result)
        except ConnectorError as exc:
            result.notes.append(f"stopped: {exc}")
        finally:
            await session.close()
        return result

    async def _measure(self, session: DeviceSession, screen: Screen, result: Result) -> None:
        if session.can(Capability.STREAM_H264):
            result.first_frame_ms = await self._first_frame(session)
        if session.can(Capability.SCREENSHOT):
            result.screenshot_900 = Timing.of(await self._shots(session, 900, 75))
            result.screenshot_160 = Timing.of(await self._shots(session, 160, 40))
        document: dict[str, Any] = {}
        if session.reader is not None:
            reader = session.reader
            await self._general(session)

            async def read() -> None:
                document.update(await reader.accessibility())

            result.snapshot = Timing.of(await _timed(self.clock, read, self.rounds))
            result.snapshot_elements = sum(1 for _ in tree_from_document(document).walk())
        if session.input is not None:
            sink = session.input
            result.input = Timing.of(
                await _timed(self.clock, lambda: sink.hid(_tap(screen.width_pt / 2, INERT_Y_PT)), self.rounds)
            )
            if session.reader is not None and session.can(Capability.SCREENSHOT):
                result.tap_to_change = await self._taps_to_change(session, result)

    async def _shots(self, session: DeviceSession, width: int, quality: int) -> list[float]:
        return await _timed(self.clock, lambda: self._shot(session, width, quality), self.rounds)

    async def _general(self, session: DeviceSession) -> ElementNode | None:
        """Settings' General row, once Settings shows it; None when it has not within `LAUNCH_WAIT_S`."""
        assert session.reader is not None
        started = self.clock()
        while True:
            tree = tree_from_document(await session.reader.accessibility())
            found = next((node for node in tree.walk() if node.label == "General" and node.frame is not None), None)
            if found is not None or self.clock() - started >= LAUNCH_WAIT_S:
                return found
            await self.sleep(0.25)

    async def _settled(self, session: DeviceSession) -> bytes:
        """A small screenshot once two in a row are the same, or the last one when the screen will not settle."""
        started = self.clock()
        shot = await self._shot(session, 160, 40)
        while self.clock() - started < SETTLE_TIMEOUT_S:
            await self.sleep(0.1)
            again = await self._shot(session, 160, 40)
            if again == shot:
                break
            shot = again
        return shot

    async def _shot(self, session: DeviceSession, width: int, quality: int) -> bytes:
        return (await session.screen.screenshot(max_width=width, quality=quality)).jpeg

    async def _first_frame(self, session: DeviceSession) -> float | None:
        started = self.clock()
        stream = session.screen.h264(fps=30, scale=0.75, key_frame_s=1.0, bitrate=3_000_000)
        try:
            await asyncio.wait_for(anext(stream), FIRST_FRAME_TIMEOUT_S)
        except (asyncio.TimeoutError, StopAsyncIteration):
            return None
        finally:
            aclose = getattr(stream, "aclose", None)
            if aclose is not None:
                await aclose()
        return round((self.clock() - started) * 1000, 1)

    async def _taps_to_change(self, session: DeviceSession, result: Result) -> Timing | None:
        assert session.reader is not None and session.input is not None
        seconds: list[float] = []
        for _ in range(self.rounds):
            await self.prepare()
            general = await self._general(session)
            if general is None or general.frame is None:
                result.notes.append("Settings showed no General row to tap")
                break
            before = await self._settled(session)
            x, y = general.frame.x + general.frame.width / 2, general.frame.y + general.frame.height / 2
            started = self.clock()
            await session.input.hid(_tap(x, y, self.sleep))
            while await self._shot(session, 160, 40) == before:
                if self.clock() - started > CHANGE_TIMEOUT_S:
                    result.notes.append(f"a tap on General did not change the screen within {CHANGE_TIMEOUT_S:g}s")
                    return Timing.of(seconds)
                await self.sleep(CHANGE_POLL_S)
            seconds.append(self.clock() - started)
        return Timing.of(seconds)


def _cell(timing: Timing | None) -> str:
    return "–" if timing is None else f"{timing.p50} / {timing.p95}"


def _ms(value: float | None) -> str:
    return "–" if value is None else str(value)


def table(results: Sequence[Result]) -> str:
    lines = [
        "| Connector | Attach ms | First H.264 frame ms | Screenshot 900 px p50 / p95 ms "
        "| Screenshot 160 px p50 / p95 ms | Snapshot p50 / p95 ms (elements) | Input p50 / p95 ms "
        "| Tap to change p50 / p95 ms |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for result in results:
        elements = "" if result.snapshot_elements is None else f" ({result.snapshot_elements})"
        lines.append(
            f"| {result.connector} | {_ms(result.attach_ms)} | {_ms(result.first_frame_ms)} "
            f"| {_cell(result.screenshot_900)} | {_cell(result.screenshot_160)} | {_cell(result.snapshot)}{elements} "
            f"| {_cell(result.input)} | {_cell(result.tap_to_change)} |"
        )
    notes = [f"- {result.connector}: {note}" for result in results for note in result.notes]
    return "\n".join([*lines, "", *notes] if notes else lines)


def parser() -> argparse.ArgumentParser:
    made = argparse.ArgumentParser(description="Measure each connector on a booted simulator.")
    made.add_argument("--device", required=True, help="the booted simulator's UDID")
    made.add_argument("--connectors", default=",".join(DEFAULT_CONNECTORS), help="which, in order (comma-separated)")
    made.add_argument("--rounds", type=int, default=10, help="calls of each measurement (default 10)")
    made.add_argument("--developer-dir", default="", help="the Xcode to use, as its Contents/Developer folder")
    made.add_argument("--helper", default="", help="the native helper to run, instead of the one found")
    made.add_argument("--json", action="store_true", help="print the results as JSON instead of a Markdown table")
    return made


def default_registry() -> ConnectorRegistry:
    context = ConnectorContext(state=AppSupportStateStore(), copy=HostCopy(), simctl_for=_simctl)
    return ConnectorRegistry.discover(context)


def _simctl(developer_dir: str) -> Simctl:
    return Simctl(run_xcrun, developer_dir=developer_dir)


async def measure(
    args: argparse.Namespace, registry: ConnectorRegistry, simctl: Simctl, clock: Clock, sleep: Sleep
) -> list[Result]:
    config = SimConfig.defaults().with_values(developer_dir=args.developer_dir, native_helper_path=args.helper)

    async def settings() -> None:
        await simctl.launch(args.device, SETTINGS_APP, terminate_running=True)
        await sleep(LAUNCH_SETTLE_S)

    bench = Bench(args.device, config, args.rounds, settings, clock, sleep)
    results: list[Result] = []
    for name in (name.strip() for name in args.connectors.split(",") if name.strip()):
        connector = registry.get(name)
        results.append(await bench.run(connector) if connector else Result(name, notes=["no such connector"]))
    return results


def main(
    argv: Sequence[str] | None = None,
    *,
    registry: Callable[[], ConnectorRegistry] = default_registry,
    simctl: Callable[[str], Simctl] | None = None,
    clock: Clock = time.perf_counter,
    sleep: Sleep = asyncio.sleep,
    out: IO[str] | None = None,
    err: IO[str] | None = None,
) -> int:
    args = parser().parse_args(argv)
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    if args.rounds < 1:
        print("--rounds is at least 1", file=err)
        return 2
    device_simctl = (simctl or _simctl)(args.developer_dir)
    try:
        results = asyncio.run(measure(args, registry(), device_simctl, clock, sleep))
    except SimctlError as exc:
        print(f"Settings could not be launched on {args.device}: {exc}", file=err)
        return 1
    print(json.dumps([asdict(result) for result in results], indent=2) if args.json else table(results), file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
