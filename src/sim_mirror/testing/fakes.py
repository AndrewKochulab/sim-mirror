# SPDX-License-Identifier: Apache-2.0
"""Fakes for everything SimMirror reaches outside itself, so no test runs xcrun, boots a device or starts a companion.

`FakeXcrun` stands where `platform.xcrun.run_xcrun` does. It records every call and answers from a script of argv
prefixes, the latest matching one winning, so a test says only what it cares about and every other call succeeds
quietly. What simctl, xcodebuild, xcresulttool and idb_companion really printed on a Mac is in `fixtures/`.

`StaticConfig` and `MemoryStateStore` are the in-memory `ConfigSource` and `StateStore` a test runs a core with;
`MemorySettingsStore` and `FakeConfirmations` stand behind the settings routes. `FakeBridge` is Xcode's tools behind
``xcrun mcpbridge``, answering as Xcode 27.0 did.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from collections.abc import AsyncIterable, AsyncIterator, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sim_mirror.config import schema
from sim_mirror.config.model import SimConfig
from sim_mirror.config.provenance import DEFAULT, SettingOrigin
from sim_mirror.connectors.base import (
    Capability,
    ConnectorReport,
    ConnectorUnavailable,
    Crop,
    DeviceSession,
    HidEvent,
    Screen,
    Shot,
)
from sim_mirror.connectors.idb.companion import Companion
from sim_mirror.platform.developer_dir import ChosenXcode
from sim_mirror.platform.xcrun import XcrunResult
from sim_mirror.protocol import PendingConfirmation
from sim_mirror.scope import Scope
from sim_mirror.seams import SettingsRefused
from sim_mirror.storage.private import ensure_private_dir

FIXTURES = Path(__file__).parent / "fixtures"

#: The booted iPhone 17 Pro in `simctl-devices.json`.
BOOTED_UDID = "D946616B-6E4F-4F5C-8C76-54FAD9B7D702"
#: The Xcode `FakeXcodeSelect` says is selected unless told otherwise.
SELECTED_XCODE = "/Applications/Xcode.app/Contents/Developer"


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

    def with_screenshot(self, image: bytes) -> FakeXcrun:
        """Answer ``simctl io <udid> screenshot --type=… <path>`` the way simctl does: write the image where it was
        told to, and nothing to stdout."""
        return self.on("simctl", "io", then=screenshot_written(image))

    def with_xcode(self, version: str = "27.0", build: str = "27A266a", *, bridge: bool = True) -> FakeXcrun:
        """Answer ``xcodebuild -version`` as this Xcode, and ``--find mcpbridge`` as one that has it -- or not."""
        found = "/Applications/Xcode27.app/Contents/Developer/usr/bin/mcpbridge"
        self.on("xcodebuild", "-version", out=f"Xcode {version}\nBuild version {build}\n")
        if bridge:
            return self.on("--find", "mcpbridge", out=found + "\n")
        return self.on("--find", "mcpbridge", rc=1, err='xcrun: error: unable to find utility "mcpbridge"')

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


BRIDGE_ANSWERS = "mcpbridge-answers.json"


@dataclass
class _Pipe:
    """A child's stdin: every line written is handed to the fake bridge."""

    bridge: FakeBridge
    process: FakeBridgeProcess
    closed: bool = False
    buffer: bytes = b""

    def write(self, data: bytes) -> None:
        if self.closed or self.process.returncode is not None:
            raise BrokenPipeError("the bridge has stopped")
        self.buffer += data
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            self.bridge.receive(self.process, json.loads(line))

    async def drain(self) -> None:
        await asyncio.sleep(0)

    def close(self) -> None:
        self.closed = True
        if self.bridge.exit_on_close:
            self.process.finish(1)


@dataclass
class FakeBridgeProcess:
    bridge: FakeBridge
    argv: tuple[str, ...]
    env: dict[str, str]
    returncode: int | None = None
    stdin: Any = None
    stdout: asyncio.StreamReader = field(default_factory=asyncio.StreamReader)
    stderr: asyncio.StreamReader = field(default_factory=asyncio.StreamReader)
    killed: bool = False

    def __post_init__(self) -> None:
        self.stdin = _Pipe(self.bridge, self)

    def say(self, message: Mapping[str, Any]) -> None:
        self.stdout.feed_data(json.dumps(message).encode() + b"\n")

    def finish(self, rc: int = 0, stderr: str = "") -> None:
        if self.returncode is not None:
            return
        if stderr:
            self.stderr.feed_data(stderr.encode() + b"\n")
        self.returncode = rc
        self.stdout.feed_eof()
        self.stderr.feed_eof()

    def kill(self) -> None:
        self.killed = True
        self.finish(-9)

    async def wait(self) -> int:
        while self.returncode is None:
            await asyncio.sleep(0)
        return self.returncode


Reply = Mapping[str, Any] | Callable[[dict[str, Any]], Mapping[str, Any] | None]


class FakeBridge:
    """Xcode's tools behind a fake ``xcrun mcpbridge``: each tool answers as Xcode 27.0 did.

    A capture writes `hierarchy` -- and the screenshot, thumbnail and log Xcode writes beside it -- into `folder`, named
    for the session. `refuse`, `reply` and `stop` change the next answers; `calls` is what was called, in order.
    """

    def __init__(self, *, folder: Path | None = None, hierarchy: str | None = None) -> None:
        self.folder = folder or Path(tempfile.mkdtemp(prefix="fake-bridge-"))
        self.folder.mkdir(parents=True, exist_ok=True)
        self.hierarchy = hierarchy if hierarchy is not None else fixture("mcpbridge-hierarchy-settings.txt")
        self.answers = fixture_json(BRIDGE_ANSWERS)
        self.processes: list[FakeBridgeProcess] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []
        #: Queued answers by tool, used before the usual one.
        self.queued: dict[str, list[Reply]] = {}
        #: Whether the bridge ends when its stdin is closed, as the real one does.
        self.exit_on_close = True
        #: Stopped on its next message, saying this on stderr.
        self.stop_saying: str | None = None
        self.captures = 0
        self.open_workspaces: list[str] = []

    async def spawn(self, argv: Sequence[str], env: Mapping[str, str]) -> FakeBridgeProcess:
        process = FakeBridgeProcess(self, tuple(argv), dict(env))
        self.processes.append(process)
        return process

    def refuse(self, tool: str, data: str) -> FakeBridge:
        """The next call of `tool` is refused, Xcode's reason in its text as JSON."""
        text = json.dumps({"type": "error", "data": data})
        return self.reply(tool, {"content": [{"type": "text", "text": text}], "isError": True})

    def reply(self, tool: str, result: Reply) -> FakeBridge:
        """The next call of `tool` answers this result -- or what a function of the arguments answers, None for
        nothing at all."""
        self.queued.setdefault(tool, []).append(result)
        return self

    def stop(self, stderr: str) -> FakeBridge:
        self.stop_saying = stderr
        return self

    def tools(self) -> list[str]:
        return [tool for tool, _ in self.calls]

    def receive(self, process: FakeBridgeProcess, message: dict[str, Any]) -> None:
        if self.stop_saying is not None:
            said, self.stop_saying = self.stop_saying, None
            process.finish(1, said)
            return
        method = message.get("method")
        if method == "initialize":
            process.say({**self.answers["initialize"], "id": message["id"]})
        elif method == "tools/call":
            params = message["params"]
            tool, arguments = params["name"], params.get("arguments", {})
            self.calls.append((tool, arguments))
            queued = self.queued.get(tool)
            reply = queued.pop(0) if queued else self._usual(tool)
            result = reply(arguments) if callable(reply) else reply
            if result is not None:
                process.say({"jsonrpc": "2.0", "id": message["id"], "result": result})

    def _usual(self, tool: str) -> Reply:
        usual: dict[str, Reply] = {
            "DeviceInteractionStartSession": self._start,
            "DeviceInteractionSynthesize": self._capture,
            "DeviceInteractionEndSession": lambda _: _structured({"userMessage": "Session stopped"}),
            "XcodeListWorkspaces": self._list,
            "XcodeOpenWorkspace": self._open,
            "XcodeCloseWorkspace": lambda _: _structured({"message": "closed"}),
        }
        return usual.get(tool, lambda _: _structured({}))

    def _start(self, arguments: dict[str, Any]) -> Mapping[str, Any]:
        answer = dict(self.answers["start_session"]["result"]["structuredContent"])
        answer.update(interactionSessionKey=arguments["sessionIdentifier"], deviceUUID=arguments["deviceIdentifier"])
        return _structured(answer)

    def _capture(self, arguments: dict[str, Any]) -> Mapping[str, Any]:
        self.captures += 1
        stem = self.folder / f"{arguments['interactSessionKey']}-{self.captures:02d}"
        paths = {
            "hierarchyPath": Path(f"{stem}-hierarchy.txt"),
            "screenshotPath": Path(f"{stem}-screenshot.png"),
            "thumbnailScreenshotPath": Path(f"{stem}-thumbnailScreenshot.png"),
            "logsPath": Path(f"{stem}-logs.txt"),
        }
        paths["hierarchyPath"].write_text(self.hierarchy, encoding="utf-8")
        for name in ("screenshotPath", "thumbnailScreenshotPath", "logsPath"):
            paths[name].write_bytes(b"")
        return _structured({"applicationState": "NotRun", **{name: str(path) for name, path in paths.items()}})

    def _list(self, arguments: dict[str, Any]) -> Mapping[str, Any]:
        listed = "\n".join(f"* workspaceIdentifier: w{n}, workspacePath: {path}" for n, path in
                           enumerate(self.open_workspaces))  # fmt: skip
        return _structured({"message": listed or "No workspaces are currently open."})

    def _open(self, arguments: dict[str, Any]) -> Mapping[str, Any]:
        path = arguments["path"]
        if path not in self.open_workspaces:
            self.open_workspaces.append(path)
        identifier = f"w{self.open_workspaces.index(path)}"
        return _structured({"workspaceIdentifier": identifier, "workspacePath": path, "activeScheme": "App"})


def _structured(content: Mapping[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(content)}], "structuredContent": dict(content)}


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


class MemorySettingsStore:
    """A `SettingsStore` over a `StaticConfig`: a change is applied to it at once and remembered as written.

    `locked` names settings a layer above the file sets, by key; `refusal`, when set, is raised by the next change.
    """

    def __init__(self, config: StaticConfig) -> None:
        self.config = config
        self.locked: dict[str, SettingOrigin] = {}
        self.refusal: SettingsRefused | None = None
        #: Each change: the scope id (None for every scope), the values set and the paths put back.
        self.changes: list[tuple[str | None, dict[str, Any], list[str]]] = []
        self._written: dict[str | None, set[str]] = {}

    def explain(self, scope: Scope) -> Mapping[str, SettingOrigin]:
        origins = {setting.key: DEFAULT for setting in schema.SETTINGS}
        for key in self._written.get(None, set()):
            origins[key] = SettingOrigin("file", "config.toml")
        for key in self._written.get(scope.id, set()):
            origins[key] = SettingOrigin("scope", f'[scopes."{scope.id}"]')
        return {**origins, **self.locked}

    def change(self, scope: Scope | None, values: Mapping[str, Any], removed: Collection[str]) -> None:
        if self.refusal is not None:
            raise self.refusal
        scope_id = None if scope is None else scope.id
        self.changes.append((scope_id, dict(values), list(removed)))
        written = self._written.setdefault(scope_id, set())
        applied: dict[str, Any] = {}
        for path, value in values.items():
            key = schema.BY_PATH[path].key
            written.add(key)
            applied[key] = value
        for path in removed:
            setting = schema.BY_PATH[path]
            written.discard(setting.key)
            applied[setting.key] = setting.default
        if scope_id is None:
            self.config.set(**applied)
        else:
            self.config.set_for(scope_id, **applied)


class FakeConfirmations:
    """`Confirmations` a test reads the codes of: one pending change per digest, confirmed by its code once."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, str]] = []
        self.codes: dict[str, str] = {}
        self._ids: dict[str, str] = {}

    def request(self, scope: Scope, digest: str, summary: str) -> PendingConfirmation:
        self.requests.append((scope.id, digest, summary))
        pending = self._ids.setdefault(digest, f"change-{len(self._ids) + 1}")
        self.codes.setdefault(digest, f"code-{pending}")
        return {"id": pending, "summary": summary, "command": "sim-mirror settings confirm", "expires_in_s": 60.0}

    def confirm(self, digest: str, code: str) -> bool:
        if self.codes.get(digest) != code:
            return False
        del self.codes[digest]
        self._ids.pop(digest, None)
        return True


def screenshot_written(image: bytes) -> Answer:
    """Play simctl writing a screenshot: the file it is given holds the image, and stdout stays empty."""

    def answer(args: tuple[str, ...]) -> XcrunResult:
        Path(args[-1]).write_bytes(image)
        return XcrunResult(0, "", f"Wrote screenshot to: {args[-1]}")

    return answer


def tiny_jpeg(width: int, height: int, body: bytes = b"") -> bytes:
    """The smallest bytes that read as a JPEG of this size: start of image, a frame header, end of image."""
    header = (
        b"\xff\xc0\x00\x11\x08"
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    )
    return b"\xff\xd8" + header + body + b"\xff\xd9"


SCREEN = Screen(width_px=1206, height_px=2622, width_pt=402, height_pt=874, scale=3.0)
JPEG = tiny_jpeg(402, 874)


class FakeEngine:
    """A running device: records input and screenshots, answers with a fixture screen."""

    def __init__(
        self,
        *,
        screen: Screen = SCREEN,
        document: dict[str, Any] | None = None,
        describe_error: Exception | None = None,
    ) -> None:
        self.screen = screen
        self.document = document if document is not None else fixture_json("ax-settings-interactable.json")
        self.describe_error = describe_error
        self.shot = Shot(JPEG, 402, 874)
        #: Raised, one per call, by the next screenshots.
        self.screenshot_errors: list[Exception] = []
        #: Raised, one per call, by the next reads of the screen.
        self.accessibility_errors: list[Exception] = []
        self.chunks: list[bytes] = []
        self.hid_events: list[HidEvent] = []
        self.screenshots: list[tuple[int, int, Crop | None]] = []
        self.closed = False

    async def describe(self) -> Screen:
        if self.describe_error is not None:
            raise self.describe_error
        return self.screen

    async def screenshot(self, *, max_width: int, quality: int, crop: Crop | None = None) -> Shot:
        self.screenshots.append((max_width, quality, crop))
        await asyncio.sleep(0)
        if self.screenshot_errors:
            raise self.screenshot_errors.pop(0)
        return self.shot

    async def _stream(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk
        await asyncio.sleep(3600)

    def h264(self, *, fps: int, scale: float, key_frame_s: float, bitrate: int) -> AsyncIterator[bytes]:
        return self._stream()

    async def hid(self, events: AsyncIterable[HidEvent]) -> None:
        async for event in events:
            self.hid_events.append(event)

    async def accessibility(self) -> dict[str, Any]:
        if self.accessibility_errors:
            raise self.accessibility_errors.pop(0)
        return self.document

    async def close(self) -> None:
        self.closed = True


class FakeCompanionProcess:
    def __init__(self, pid: int = 4242) -> None:
        self.pid = pid
        self.returncode: int | None = None


class FakeLauncher:
    """Starts a companion without a process: `hold` keeps a start waiting until `release` is set."""

    def __init__(self, engine: FakeEngine | None = None, *, fail: Exception | None = None, hold: bool = False) -> None:
        self.engine = engine or FakeEngine()
        self.fail = fail
        self.release = asyncio.Event() if hold else None
        self.started: list[tuple[str, str]] = []
        #: The Xcode each start was told to run with, in the order of `started`.
        self.developer_dirs: list[str] = []
        self.stopped: list[str] = []
        self.reaped = 0

    async def start(self, binary: str, udid: str, developer_dir: str = "") -> Companion:
        self.started.append((binary, udid))
        self.developer_dirs.append(developer_dir)
        if self.release is not None:
            await self.release.wait()
        if self.fail is not None:
            raise self.fail
        return Companion(
            udid=udid,
            process=FakeCompanionProcess(),
            socket=Path("/fake/c.sock"),
            pid_file=Path("/fake/c.pid"),
            engine=self.engine,
            developer_dir=developer_dir,
        )

    async def stop(self, companion: Companion) -> None:
        self.stopped.append(companion.udid)
        companion.process.returncode = 0

    async def reap_orphans(self) -> int:
        self.reaped += 1
        return 2


class FakeXcodeSelect:
    """Which Xcode a companion runs with, answered without asking the Mac: the setting, else `selected`.

    It stands where `platform.developer_dir.choose_xcode` does. An empty `selected` is a Mac with no Xcode selected.
    """

    def __init__(self, selected: str = SELECTED_XCODE) -> None:
        self.selected = selected
        self.asked: list[str] = []

    async def __call__(self, configured: str) -> ChosenXcode | None:
        self.asked.append(configured)
        if configured:
            return ChosenXcode(configured, "setting")
        return ChosenXcode(self.selected, "xcode-select") if self.selected else None


class FakeKeyboard:
    """The Mac's keyboard layout, answered without asking the Mac: US-shaped or not, as a test says.

    It stands where `platform.keyboard.mac_keyboard_is_us` does, and counts how often it was asked -- text that has no
    keys, or a scope that always pastes, never asks.
    """

    def __init__(self, us: bool = False) -> None:
        self.us = us
        self.asked = 0

    async def __call__(self) -> bool:
        self.asked += 1
        return self.us


#: Everything a device can do, as the idb connector offers it.
FULL_CONTROL = frozenset(Capability) - {Capability.BUILD_PREVIEW}


class FakeConnector:
    """A connector over a `FakeEngine`: available or not, with the capabilities a test gives it."""

    def __init__(
        self,
        name: str = "fake",
        *,
        engine: FakeEngine | None = None,
        capabilities: frozenset[Capability] = FULL_CONTROL,
        available: bool = True,
        reasons: tuple[str, ...] = (),
        fail: Exception | None = None,
        hold: bool = False,
        fps_limit: int | None = None,
    ) -> None:
        self.name = name
        self.engine = engine or FakeEngine()
        self.capabilities = capabilities
        self.available = available
        self.reasons = reasons if reasons or available else (f"the {name} connector is switched off in this test",)
        self.fail = fail
        self.release = asyncio.Event() if hold else None
        self.fps_limit = fps_limit
        self.attached: list[str] = []
        self.closed: list[str] = []
        self.sessions: list[DeviceSession] = []
        self.alive = True
        #: Devices whose session has stopped, as a helper process that exited would; attaching again revives one.
        self.dead: set[str] = set()
        self.reaped = 0

    async def probe(self, config: SimConfig) -> ConnectorReport:
        if not self.available:
            return ConnectorReport(self.name, False, reasons=self.reasons)
        return ConnectorReport(self.name, True, self.capabilities, {"fake": "1"})

    async def attach(self, udid: str, config: SimConfig) -> DeviceSession:
        self.attached.append(udid)
        if self.release is not None:
            await self.release.wait()
        if self.fail is not None:
            raise self.fail
        if not self.available:
            raise ConnectorUnavailable(" ".join(self.reasons), 409)

        async def close() -> None:
            self.closed.append(udid)

        def alive() -> bool:
            return self.alive and udid not in self.dead

        self.dead.discard(udid)
        control = self.capabilities & FULL_CONTROL
        session = DeviceSession(
            connector=self.name,
            capabilities=self.capabilities,
            screen=self.engine,
            input=self.engine if control & {Capability.INPUT_TOUCH, Capability.INPUT_KEY} else None,
            reader=self.engine if Capability.ELEMENT_TREE in control else None,
            fps_limit=self.fps_limit,
            is_alive=alive,
            on_close=close,
        )
        self.sessions.append(session)
        return session

    async def reap_orphans(self) -> int:
        self.reaped += 1
        return 0


class FakePolicy:
    """A `Policy` a test sets directly."""

    def __init__(
        self,
        *,
        area: bool = True,
        shells: bool = True,
        roots: tuple[Path, ...] = (),
        folder: Path | None = None,
    ) -> None:
        self.area = area
        self.shells = shells
        self.roots = roots
        self.folder = folder

    def area_enabled(self, scope: Scope) -> bool:
        return self.area

    def shells_allowed(self, scope: Scope) -> bool:
        return self.shells

    def install_roots(self, scope: Scope) -> tuple[Path, ...]:
        return self.roots

    def build_folder(self, scope: Scope) -> Path | None:
        return self.folder


class MemoryStateStore:
    """A `StateStore` whose every folder is under one root a test owns."""

    def __init__(self, root: Path, *, owner_tag: str = "SimMirrorTest") -> None:
        self.root = root
        self._owner_tag = owner_tag

    @property
    def owner_tag(self) -> str:
        return self._owner_tag

    def devices_file(self) -> Path:
        """Where a test's `JsonDeviceMemory` would keep its file. Not a seam, as on the real store: a host that
        remembers devices its own way never answers this."""
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
