# SPDX-License-Identifier: Apache-2.0
"""Recording a device's screen, from the viewer or an agent, kept as an MP4, a GIF or both, with its touches drawn in.

A recording is one device's at a time. It takes its pictures from the device's own tool when that can record -- a
simulator, through simctl -- and otherwise from the frames SimMirror already streams (`recording_sources`). While it
runs it notes every touch that lands, the agent's and a person's, with its time; when it stops, the native helper
renders what was recorded into what the settings ask for -- sped up, a GIF at its own size and rate, each touch drawn
where and when it landed (`Renderer`) -- and it is kept in the recordings folder, private to the person, beside a note
of what it is. Without the helper, a simulator's movie is kept as it was recorded, and its note says why nothing was
drawn.

A recording stops when it is asked to, after ``recording.max_seconds``, or when its device is let go; rendering runs
after it stops, so letting a device go never waits for it. The oldest recordings beyond ``recording.keep`` go.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import Capability, Screen
from sim_mirror.core.control import StatusBar
from sim_mirror.core.instance import DeviceInstance
from sim_mirror.core.recording_sources import (
    START_TIMEOUT_S,
    RecordingSource,
    SourceKind,
    StreamRecording,
    ToolRecording,
)
from sim_mirror.platform.errors import DeviceControlError
from sim_mirror.platform.simctl import RECORDING_STARTED
from sim_mirror.protocol import Recording, RecordingFile, RecordingState
from sim_mirror.storage.private import ensure_private_dir

logger = logging.getLogger(__name__)

Format = Literal["mp4", "gif", "both"]
FORMATS: tuple[Format, ...] = ("mp4", "gif", "both")
SPEEDS = ("1", "1.5", "2", "4")
#: A kept recording's file name: when it began, the device's name made safe for a file, and what it is.
FILE_NAME = re.compile(r"\A[0-9]{8}-[0-9]{6}-[a-z0-9-]{1,64}\.(mp4|gif)\Z")
_SLUG = re.compile(r"[^a-z0-9]+")

#: Starts a device's own tool recording its screen into a path, logging to another, with a codec; answers the process.
ToolRecorder = Callable[[DeviceInstance, Path, Path, str], Awaitable[Any]]


class RecordingRefused(Exception):
    """Why a recording cannot be started, stopped or kept, said so a person or an agent can act on it."""


@dataclass(frozen=True)
class RecordingOptions:
    """How one recording is kept: the scope's settings, with what the call asked for in their place."""

    format: Format
    codec: str
    touches: bool
    speed: float
    gif_fps: int
    gif_width: int
    status_bar: bool
    max_seconds: int

    @classmethod
    def from_config(
        cls,
        config: SimConfig,
        *,
        format: str | None = None,
        touches: bool | None = None,
        speed: str | None = None,
        status_bar: bool | None = None,
    ) -> RecordingOptions:
        chosen = format or config.recording_format
        if chosen not in FORMATS:
            raise RecordingRefused(f"format is one of {', '.join(FORMATS)}")
        pace = speed or config.recording_speed
        if pace not in SPEEDS:
            raise RecordingRefused(f"speed is one of {', '.join(SPEEDS)}")
        return cls(
            format=chosen,
            codec=config.recording_codec,
            touches=config.recording_touches if touches is None else touches,
            speed=float(pace),
            gif_fps=config.recording_gif_fps,
            gif_width=config.recording_gif_width,
            status_bar=config.recording_status_bar if status_bar is None else status_bar,
            max_seconds=config.recording_max_seconds,
        )


@dataclass(frozen=True)
class Touch:
    """A touch that landed while recording: when, in seconds after the first frame; what; where, each point a
    fraction of the screen's width and height; for how long; and whose."""

    t: float
    kind: str
    points: tuple[tuple[float, float], ...]
    duration: float
    by: Literal["agent", "person"]


@dataclass(frozen=True)
class RenderJob:
    """What the native helper is asked to render."""

    input: Path
    input_kind: SourceKind
    touches: tuple[Touch, ...]
    mp4: Path | None
    gif: Path | None
    codec: str
    speed: float
    gif_fps: int
    gif_width: int

    def to_json(self) -> dict[str, Any]:
        return {
            "input": str(self.input),
            "input_kind": self.input_kind,
            "touches": [asdict(touch) for touch in self.touches],
            "mp4": None if self.mp4 is None else str(self.mp4),
            "gif": None if self.gif is None else str(self.gif),
            "codec": self.codec,
            "speed": self.speed,
            "gif_fps": self.gif_fps,
            "gif_width": self.gif_width,
        }


@dataclass(frozen=True)
class Rendered:
    """One file the helper wrote."""

    path: Path
    format: Literal["mp4", "gif"]
    width: int
    height: int
    duration_s: float


class Renderer(Protocol):
    async def why_not(self, config: SimConfig) -> str | None:
        """Why this Mac cannot render recordings, or None when it can."""
        ...

    async def render(self, job: RenderJob, config: SimConfig) -> list[Rendered]:
        """Render a recording into the files the job names. Raises `RecordingRefused` when it cannot."""
        ...


def default_folder(config: SimConfig) -> Path:
    """Where recordings are kept: ``recording.folder``, else SimMirror in the person's Movies folder."""
    return Path(config.recording_folder) if config.recording_folder else Path.home() / "Movies" / "SimMirror"


def recording_stem(started: datetime, device: str) -> str:
    """A recording's name, before what it is kept as: when it began, and the device's name made safe for a file."""
    slug = _SLUG.sub("-", device.lower()).strip("-")[:64] or "device"
    return f"{started:%Y%m%d-%H%M%S}-{slug}"


@dataclass(eq=False)
class RecordingRun:
    """A recording under way on one device."""

    owner: Recordings
    id: str
    instance: DeviceInstance
    source: RecordingSource
    control: StatusBar
    options: RecordingOptions
    config: SimConfig
    folder: Path
    by: str
    started_at: datetime
    began: float
    touches: list[Touch] = field(default_factory=list)
    #: Whether this recording gave the device its demo status bar, to take away when it stops.
    status_bar: bool = False
    watchdog: asyncio.Task[None] | None = None
    #: Keeping it, once it has stopped.
    finishing: asyncio.Task[Recording] | None = None
    #: A person's finger on the screen: when it went down, and the points it has passed.
    finger: tuple[float, list[tuple[float, float]]] | None = None

    def state(self, now: float) -> RecordingState:
        return {
            "id": self.id,
            "since_ms": max(0, round((now - self.began) * 1000)),
            "by": self.by,
            "max_ms": self.options.max_seconds * 1000,
        }

    def _where(self, x: float, y: float) -> tuple[float, float]:
        screen: Screen | None = self.instance.screen
        if screen is None:
            return (0.0, 0.0)
        return (round(x / screen.width_pt, 4), round(y / screen.height_pt, 4))

    def agent(self, kind: str, points: Sequence[tuple[float, float]], duration: float, lead: float) -> None:
        """An agent's gesture, its points in the device's points, landing `lead` seconds from now."""
        self.touches.append(
            Touch(
                round(self.owner.now() + lead - self.began, 3),
                kind,
                tuple(self._where(x, y) for x, y in points),
                round(duration, 3),
                "agent",
            )
        )

    def person(self, phase: str, x: float, y: float) -> None:
        """A person's finger, in the device's points: down, moved, or lifted."""
        now = self.owner.now()
        point = self._where(x, y)
        if phase == "down":
            self.finger = (now, [point])
            return
        if self.finger is None:
            return
        began, points = self.finger
        points.append(point)
        if phase in ("up", "cancel"):
            self.finger = None
            trail = tuple(dict.fromkeys(points))
            kind = "tap" if len(trail) == 1 else "swipe"
            self.touches.append(Touch(round(began - self.began, 3), kind, trail, round(now - began, 3), "person"))

    async def end(self) -> None:
        """Stop because the device is let go: it is still kept, rendered in the background."""
        if self.instance.recording is self:
            self.instance.recording = None
        await self.owner.finish(self)


class Recordings:
    """Every device's recording, for one process."""

    def __init__(
        self,
        renderer: Renderer,
        *,
        tool_recorder: ToolRecorder | None = None,
        folder_for: Callable[[SimConfig], Path] = default_folder,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        start_timeout: float = START_TIMEOUT_S,
    ) -> None:
        self._renderer = renderer
        #: Starts a simulator's own recording, through simctl; None where only streams are recorded.
        self._tool_recorder = tool_recorder
        self._folder_for = folder_for
        self._clock = clock
        self._wall = wall
        self._sleep = sleep
        #: How long a device's own tool may take to take its first frame.
        self._start_timeout = start_timeout

    def now(self) -> float:
        return self._clock()

    def folder(self, config: SimConfig) -> Path:
        return ensure_private_dir(self._folder_for(config))

    async def start(
        self, instance: DeviceInstance, config: SimConfig, control: StatusBar, *, by: str, options: RecordingOptions
    ) -> RecordingRun:
        """Begin recording the device's screen. Refuses while one is under way or the screen cannot be had."""
        if instance.recording is not None:
            raise RecordingRefused(f"{instance.name} is already being recorded; stop that recording first")
        if instance.hub is None or instance.session is None:
            raise RecordingRefused(f"{instance.name} is not showing its screen yet; try again once it is ready")
        folder = self.folder(config)
        started_at = self._wall()
        # Named in the person's own time, as the files they will look for are; the note keeps UTC.
        stem = recording_stem(started_at.astimezone(), instance.name)
        source = self._source(instance, folder / f".{stem}.raw", options)
        if source.kind == "frames":
            why = await self._renderer.why_not(config)
            if why:
                raise RecordingRefused(f"recording {instance.name} needs SimMirror's native helper: {why}")
        status_bar = await self._demo_status_bar(instance, control, options)
        try:
            began = await source.start()
        except DeviceControlError as exc:
            if status_bar:
                await _own_status_bar(instance, control)
            raise RecordingRefused(str(exc)) from exc
        run = RecordingRun(
            owner=self,
            id=stem,
            instance=instance,
            source=source,
            control=control,
            options=options,
            config=config,
            folder=folder,
            by=by,
            started_at=started_at,
            began=began,
            status_bar=status_bar,
        )
        run.watchdog = asyncio.get_running_loop().create_task(self._stop_after(run))
        instance.recording = run
        self._tell(instance)
        return run

    def _source(self, instance: DeviceInstance, raw: Path, options: RecordingOptions) -> RecordingSource:
        if self._tool_recorder is not None and instance.kind == "simulator":
            recorder = self._tool_recorder

            async def begin(path: Path, log: Path) -> Any:
                return await recorder(instance, path, log, options.codec)

            return ToolRecording(
                begin,
                raw.with_suffix(".mp4"),
                raw.with_suffix(".log"),
                started=RECORDING_STARTED,
                clock=self._clock,
                start_timeout=self._start_timeout,
            )
        assert instance.hub is not None and instance.session is not None
        encoding = "h264" if instance.session.can(Capability.STREAM_H264) else "jpeg"
        return StreamRecording(instance.hub, raw, encoding=encoding, clock=self._clock)

    @staticmethod
    async def _demo_status_bar(instance: DeviceInstance, control: StatusBar, options: RecordingOptions) -> bool:
        """Give the device a demo status bar for the recording, when asked and it has not got one already."""
        assert instance.session is not None
        if not options.status_bar or instance.demo_status_bar or not instance.session.can(Capability.STATUS_BAR):
            return False
        try:
            await control.demo_status_bar(instance.udid)
        except DeviceControlError as exc:
            logger.info("recording %s without a demo status bar: %s", instance.udid, exc)
            return False
        return True

    def _tell(self, instance: DeviceInstance) -> None:
        """Every screen watching sees whether the device is being recorded, and by whom."""
        instance.events.publish({"type": "status", **instance.describe(self._clock())})

    async def _stop_after(self, run: RecordingRun) -> None:
        await self._sleep(run.options.max_seconds)
        if run.instance.recording is run:
            run.instance.recording = None
            self._tell(run.instance)
            await self.finish(run)

    async def stop(self, instance: DeviceInstance) -> Recording:
        """Stop the device's recording and keep it as its options say."""
        run = instance.recording
        if run is None or not isinstance(run, RecordingRun):
            raise RecordingRefused(f"{instance.name} is not being recorded")
        instance.recording = None
        self._tell(instance)
        await self.finish(run)
        assert run.finishing is not None
        return await run.finishing

    async def finish(self, run: RecordingRun) -> None:
        """Stop the recording's source and start keeping it. Finishing twice is finishing once."""
        if run.finishing is not None:
            return
        if run.watchdog is not None and run.watchdog is not asyncio.current_task():
            run.watchdog.cancel()
        duration = self._clock() - run.began
        failure: DeviceControlError | None = None
        try:
            await run.source.stop()
        except DeviceControlError as exc:
            failure = exc
        if run.status_bar:
            await _own_status_bar(run.instance, run.control)
        run.finishing = asyncio.get_running_loop().create_task(self._keep(run, duration, failure))

    async def _keep(self, run: RecordingRun, duration: float, failure: DeviceControlError | None) -> Recording:
        """Render the recording, keep it with its note, and let the oldest go."""
        try:
            if failure is not None:
                raise RecordingRefused(str(failure))
            rendered, notes = await self._render(run, duration)
        finally:
            _forget(run.source.raw)
        files: list[RecordingFile] = [_file(made) for made in rendered]
        kept: Recording = {
            "id": run.id,
            "device": run.instance.name,
            "started_at": run.started_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "duration_ms": round(max(made.duration_s for made in rendered) * 1000),
            "files": files,
            "notes": notes,
        }
        _private_write(run.folder / f"{run.id}.json", json.dumps(kept, indent=1))
        self.prune(run.config)
        return kept

    async def _render(self, run: RecordingRun, duration: float) -> tuple[list[Rendered], list[str]]:
        options, folder = run.options, run.folder
        mp4 = folder / f"{run.id}.mp4" if options.format in ("mp4", "both") else None
        job = RenderJob(
            input=run.source.raw,
            input_kind=run.source.kind,
            touches=tuple(run.touches) if options.touches else (),
            mp4=mp4,
            gif=folder / f"{run.id}.gif" if options.format in ("gif", "both") else None,
            codec=options.codec,
            speed=options.speed,
            gif_fps=options.gif_fps,
            gif_width=options.gif_width,
        )
        try:
            return await self._renderer.render(job, run.config), []
        except RecordingRefused as exc:
            if run.source.kind != "movie" or mp4 is None:
                raise
            # A simulator's own movie is kept as it was recorded when it cannot be rendered.
            os.replace(run.source.raw, mp4)
            said = f"kept as recorded, without its touches drawn or sped up: {exc}"
            return [Rendered(mp4, "mp4", 0, 0, duration)], [said]

    def prune(self, config: SimConfig) -> list[str]:
        """Let the oldest recordings go beyond ``recording.keep``. Answers the ids that went."""
        gone = self.listing(config)[config.recording_keep :]
        folder = self._folder_for(config)
        for recording in gone:
            for kept in recording["files"]:
                with contextlib.suppress(OSError):
                    (folder / kept["name"]).unlink()
            with contextlib.suppress(OSError):
                (folder / f"{recording['id']}.json").unlink()
        return [recording["id"] for recording in gone]

    def listing(self, config: SimConfig) -> list[Recording]:
        """The recordings kept, newest first."""
        folder = self._folder_for(config)
        kept: list[Recording] = []
        for note in folder.glob("*.json") if folder.is_dir() else ():
            with contextlib.suppress(OSError, ValueError):
                recording = json.loads(note.read_text(encoding="utf-8"))
                if _is_recording(recording):
                    kept.append(recording)
        return sorted(kept, key=lambda recording: recording["started_at"], reverse=True)

    def file(self, config: SimConfig, name: str) -> Path | None:
        """A kept recording's file by its name, or None: nothing outside the folder, and nothing but a recording."""
        if not FILE_NAME.match(name):
            return None
        path = self._folder_for(config) / name
        return path if path.is_file() else None


def _is_recording(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("id"), str)
        and isinstance(value.get("started_at"), str)
        and isinstance(value.get("files"), list)
        and all(isinstance(entry, dict) and FILE_NAME.match(str(entry.get("name", ""))) for entry in value["files"])
    )


def _file(made: Rendered) -> RecordingFile:
    with contextlib.suppress(OSError):
        os.chmod(made.path, 0o600)
    return {
        "name": made.path.name,
        "path": str(made.path),
        "format": made.format,
        "bytes": made.path.stat().st_size if made.path.is_file() else 0,
        "width": made.width,
        "height": made.height,
    }


async def _own_status_bar(instance: DeviceInstance, control: StatusBar) -> None:
    try:
        await control.clear_status_bar(instance.udid)
    except DeviceControlError as exc:
        logger.warning("could not give %s its own status bar back: %s", instance.udid, exc)


def _private_write(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write(text)


def _forget(raw: Path) -> None:
    for leftover in (raw, raw.with_suffix(".log")):
        with contextlib.suppress(OSError):
            leftover.unlink()
