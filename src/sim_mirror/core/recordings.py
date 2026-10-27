# SPDX-License-Identifier: Apache-2.0
"""Recording a device's screen, from the viewer or an agent, kept as an MP4, a GIF or both, with its touches drawn in.

A recording is one device's at a time. It takes its pictures from the device's own tool when that can record -- a
simulator, through simctl -- and otherwise from the frames SimMirror already streams (`recording_sources`). While it
runs it notes every touch that lands, the agent's and a person's, with its time; when it stops, the native helper
renders what was recorded into what the settings ask for -- sped up, a GIF at its own size and rate, each touch drawn
where and when it landed (`Renderer`) -- and it is kept in the recordings folder, private to the person, beside a note
of what it is. Without the helper, a simulator's movie is kept as it was recorded, and its note says why nothing was
drawn.

A recording stops when it is asked to, after ``recording.max_seconds`` -- or the ``max_seconds`` its call gave -- or
when its device is let go; rendering runs after it stops, so letting a device go never waits for it. The oldest
recordings beyond ``recording.keep`` go.

**Kept whole, or not at all.** Its files run as long as it ran by the clock, its last picture held to the end, however
long the screen stood still. The helper renders each into a hidden partial file, which takes its name only once it is
finished, and the note beside it is written in one step; what a failed render leaves is removed, and the raw recording
is kept and said. A recording that stopped by itself is remembered for the device, so the next `stop` answers it rather
than that nothing is being recorded. `stop` waits a while for rendering, then answers that it goes on and where it will
be kept; until it is, `finishing` names it.

**A copy beside it.** A recording can also be kept as a preset -- ``also: "pr"`` -- beside the files its options give:
the full-size MP4 for the person, say, and ``<id>.pr.mp4``, 450 wide, for the pull request. The copy is rendered from
the same recording, with the same touches and speed, after the files it sits beside; one that cannot be rendered is a
note, and the rest is kept.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field, replace
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
from sim_mirror.protocol import Recording, RecordingFile, RecordingFinishing, RecordingState, RecordingStopped
from sim_mirror.storage.private import ensure_private_dir, write_atomic

logger = logging.getLogger(__name__)

Format = Literal["mp4", "gif", "both"]
FORMATS: tuple[Format, ...] = ("mp4", "gif", "both")
SPEEDS = ("1", "1.5", "2", "4")
#: How many bits an MP4 spends on each pixel every second (`recording.mp4_quality`); the helper says how many.
QUALITIES = ("low", "medium", "high")
#: How wide a call may ask a recording's files to be, as `recording.gif_width` allows.
WIDTHS = (240, 1200)
#: What a preset gives a recording, each in place of its setting; what the call says besides comes before it. ``pr``
#: is an MP4 a pull request plays inline: 450 wide, H.264, which every browser plays, at its lowest bitrate.
PRESETS: dict[str, dict[str, Any]] = {"pr": {"format": "mp4", "codec": "h264", "width": 450, "quality": "low"}}
#: The largest GIF GitHub shows inline in a pull request or an issue, in bytes; a larger one is only a download.
GIF_INLINE_MAX = 10_000_000
#: The largest video GitHub takes into a pull request or an issue on its free plan, in bytes.
VIDEO_INLINE_MAX = 10_000_000
#: A kept recording's file name: when it began, the device's name made safe for a file, and what it is.
FILE_NAME = re.compile(r"\A[0-9]{8}-[0-9]{6}-[a-z0-9-]{1,64}(?:\.[a-z]{1,16})?\.(mp4|gif)\Z")
_SLUG = re.compile(r"[^a-z0-9]+")
#: How long a recording may run before it stops by itself, in seconds: what ``recording.max_seconds`` and a call's own
#: ``max_seconds`` may be.
MAX_SECONDS = (5, 3600)
#: How long a label a call gives may be, and how much of it, made safe for a file, joins the recording's name.
LABEL_LENGTH = 64
LABEL_KEPT = 40
#: How long `stop` waits for a recording to be rendered before it answers that it still is; rendering goes on.
STOP_WAIT_S = 60.0
#: What a file being rendered is called until it is finished: hidden, so nothing serves or lists it half written.
PARTIAL = "partial"

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
    #: How wide the MP4 is, no wider than the screen; 0 is the screen's own width.
    mp4_width: int = 0
    mp4_quality: str = "high"
    #: The presets a copy is also kept as, beside these files (`PRESETS`).
    also: tuple[str, ...] = ()
    #: What the call named it, made safe for a file: joined into its name after the device's (`recording_stem`).
    label: str = ""

    @classmethod
    def from_config(
        cls,
        config: SimConfig,
        *,
        format: str | None = None,
        touches: bool | None = None,
        speed: str | None = None,
        status_bar: bool | None = None,
        width: int | None = None,
        quality: str | None = None,
        preset: str | None = None,
        also: str | None = None,
        max_seconds: int | None = None,
        label: str | None = None,
    ) -> RecordingOptions:
        """The scope's settings, with a preset's in their place, and what the call asked for in place of both."""
        if preset is not None and preset not in PRESETS:
            raise RecordingRefused(f"preset is one of {', '.join(PRESETS)}")
        copies = also_kept(also)
        shortest, longest = MAX_SECONDS
        if max_seconds is not None and (
            isinstance(max_seconds, bool) or not isinstance(max_seconds, int) or not shortest <= max_seconds <= longest
        ):
            raise RecordingRefused(f"max_seconds is a whole number of seconds from {shortest} to {longest}")
        named = label_slug(label)
        given = PRESETS[preset] if preset else {}
        chosen = format or given.get("format") or config.recording_format
        if chosen not in FORMATS:
            raise RecordingRefused(f"format is one of {', '.join(FORMATS)}")
        pace = speed or config.recording_speed
        if pace not in SPEEDS:
            raise RecordingRefused(f"speed is one of {', '.join(SPEEDS)}")
        wide = given.get("width") if width is None else width
        low, high = WIDTHS
        if wide is not None and (isinstance(wide, bool) or not isinstance(wide, int) or not low <= wide <= high):
            raise RecordingRefused(f"width is a whole number of pixels from {low} to {high}")
        bits = quality or given.get("quality") or config.recording_mp4_quality
        if bits not in QUALITIES:
            raise RecordingRefused(f"quality is one of {', '.join(QUALITIES)}")
        return cls(
            format=chosen,
            codec=given.get("codec") or config.recording_codec,
            touches=config.recording_touches if touches is None else touches,
            speed=float(pace),
            gif_fps=config.recording_gif_fps,
            gif_width=config.recording_gif_width if wide is None else wide,
            status_bar=config.recording_status_bar if status_bar is None else status_bar,
            max_seconds=config.recording_max_seconds if max_seconds is None else max_seconds,
            mp4_width=config.recording_mp4_width if wide is None else wide,
            mp4_quality=bits,
            also=copies,
            label=named,
        )

    def as_preset(self, preset: str) -> RecordingOptions:
        """These options as a preset gives them: how a copy kept beside the recording is rendered."""
        given = PRESETS[preset]
        return replace(
            self,
            format=given["format"],
            codec=given["codec"],
            mp4_width=given["width"],
            gif_width=given["width"],
            mp4_quality=given["quality"],
            also=(),
        )


def also_kept(also: object) -> tuple[str, ...]:
    """The presets a call asks a copy to be kept as, beside the recording: one of `PRESETS`, or none."""
    if also is None:
        return ()
    if not isinstance(also, str) or also not in PRESETS:
        raise RecordingRefused(f"also is one of {', '.join(PRESETS)}: a copy kept beside the recording")
    return (also,)


def label_slug(label: object) -> str:
    """A call's label made safe for a file name -- lower case letters, digits and dashes, cut to `LABEL_KEPT` -- or ""
    when it gave none. Never a path: everything else becomes a dash."""
    if label is None:
        return ""
    if not isinstance(label, str) or len(label) > LABEL_LENGTH:
        raise RecordingRefused(f"label is text of up to {LABEL_LENGTH} characters, joined into the file's name")
    slug = _SLUG.sub("-", label.lower()).strip("-")[:LABEL_KEPT].rstrip("-")
    if not slug:
        raise RecordingRefused("label needs a letter or a digit: it is joined into the file's name")
    return slug


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
    #: 0 keeps the recording's own width. A helper built before these two ignores them and renders full size.
    mp4_width: int = 0
    mp4_quality: str = "high"
    #: How long the recording ran by the clock, in seconds: its files run that long, however long ago the screen last
    #: changed. 0 is as long as its pictures run, as a helper built before it renders every recording.
    duration_s: float = 0.0

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
            "mp4_width": self.mp4_width,
            "mp4_quality": self.mp4_quality,
            "duration_s": round(self.duration_s, 3),
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


def recording_stem(started: datetime, device: str, label: str = "", nth: int = 1) -> str:
    """A recording's name, before what it is kept as: when it began, and the device's name made safe for a file --
    then the call's label (`label_slug`), then, for the `nth` recording of that name, ``-<nth>``; the device's name
    cut short to leave them room."""
    tail = (f"-{label}" if label else "") + (f"-{nth}" if nth > 1 else "")
    slug = _SLUG.sub("-", device.lower()).strip("-")
    if tail:
        slug = slug[: 64 - len(tail)].rstrip("-")
    return f"{started:%Y%m%d-%H%M%S}-{slug[:64] or 'device'}{tail}"


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
    #: How long it recorded, in seconds, once it has stopped.
    recorded_s: float = 0.0
    #: Whether it stopped by itself, at its limit, rather than when asked or let go.
    by_itself: bool = False
    #: Where its files will be kept, once it has stopped.
    paths: tuple[Path, ...] = ()

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

    def finishing_state(self) -> RecordingFinishing:
        """This recording as it stands once stopped and still being rendered."""
        return {
            "id": self.id,
            "device": self.instance.name,
            "started_at": _when(self.started_at),
            "recorded_ms": round(self.recorded_s * 1000),
            "stopped_by_itself": self.by_itself,
            "paths": [str(path) for path in self.paths],
        }


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
        stop_wait: float = STOP_WAIT_S,
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
        #: How long `stop` waits for rendering before it answers that it goes on.
        self._stop_wait = stop_wait
        #: The recordings stopped and not yet kept, by id.
        self._finishing: dict[str, RecordingRun] = {}
        #: Each device's recording that stopped by itself, by UDID, until a `stop` answers it or another begins.
        self._stopped: dict[str, RecordingRun] = {}
        #: The folder and id of each recording begun and not yet kept: no other in that folder takes its id.
        self._taken: set[tuple[Path, str]] = set()

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
        stem = self._name(folder, started_at, instance.name, options.label)
        try:
            source = self._source(instance, folder / f".{stem}.raw", options)
            if source.kind == "frames":
                why = await self._renderer.why_not(config)
                if why:
                    raise RecordingRefused(f"recording {instance.name} needs SimMirror's native helper: {why}")
            status_bar = await self._demo_status_bar(instance, control, options)
            try:
                began = await source.start()
            except DeviceControlError as exc:
                _forget(source.raw)
                if status_bar:
                    await _own_status_bar(instance, control)
                raise RecordingRefused(str(exc)) from exc
        except BaseException:
            self._taken.discard((folder, stem))
            raise
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
        # A new recording is what the next stop answers, not one that stopped by itself before it.
        self._stopped.pop(instance.udid, None)
        run.watchdog = asyncio.get_running_loop().create_task(self._stop_after(run))
        instance.recording = run
        self._tell(instance)
        return run

    def _name(self, folder: Path, started_at: datetime, device: str, label: str) -> str:
        """A name no other recording in the folder has -- kept, being rendered or under way -- taken at once, so two
        begun in the same second, of one device or two of the same name, never write over each other."""
        nth = 1
        while True:
            # Named in the person's own time, as the files they will look for are; the note keeps UTC.
            stem = recording_stem(started_at.astimezone(), device, label, nth)
            taken = (folder, stem) in self._taken
            if not taken and not any(folder.glob(f"{stem}.*")) and not any(folder.glob(f".{stem}.*")):
                self._taken.add((folder, stem))
                return stem
            nth += 1

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
            run.by_itself = True
            self._stopped[run.instance.udid] = run
            limit = run.options.max_seconds
            logger.info("recording %s stopped by itself after %ss, its limit; it is kept once rendered", run.id, limit)
            self._tell(run.instance)
            await self.finish(run)

    def ended(self, instance: DeviceInstance) -> RecordingRun | None:
        """The device's recording that stopped by itself, which the next `stop` answers; None when there is none."""
        return self._stopped.get(instance.udid)

    async def stop(self, instance: DeviceInstance, *, also: str | None = None) -> RecordingStopped:
        """Stop the device's recording and keep it as its options say -- with a copy as the preset `also` names
        beside it, too. One that stopped by itself is answered as it was kept. Answers once it is kept, or -- when
        rendering takes longer than the wait -- that it is still being rendered and where it will be kept."""
        copies = also_kept(also)
        run = instance.recording
        if run is None or not isinstance(run, RecordingRun):
            ended = self._stopped.pop(instance.udid, None)
            if ended is None:
                raise RecordingRefused(f"{instance.name} is not being recorded")
            late = [preset for preset in copies if preset not in ended.options.also]
            return await self._answer(ended, late=late)
        if copies:
            run.options = replace(run.options, also=tuple(dict.fromkeys((*run.options.also, *copies))))
        instance.recording = None
        self._tell(instance)
        await self.finish(run)
        return await self._answer(run)

    async def _answer(self, run: RecordingRun, *, late: Sequence[str] = ()) -> RecordingStopped:
        """What `stop` says of a stopped recording: kept, once it is, or still being rendered after the wait."""
        assert run.finishing is not None
        rendering = False
        try:
            kept = await asyncio.wait_for(asyncio.shield(run.finishing), self._stop_wait)
        except asyncio.TimeoutError:
            kept, rendering = self._unkept(run), True
        except RecordingRefused as exc:
            if run.by_itself:
                raise RecordingRefused(f"it stopped by itself at {run.recorded_s:.0f}s, its limit; {exc}") from exc
            raise
        if late:
            said = [
                f"no {preset} copy: the recording had stopped by itself before one was asked for" for preset in late
            ]
            kept = {**kept, "notes": [*kept["notes"], *said]}
        return {
            "recording": kept,
            "recorded_ms": round(run.recorded_s * 1000),
            "stopped_by_itself": run.by_itself,
            "rendering": rendering,
        }

    def _unkept(self, run: RecordingRun) -> Recording:
        """A recording still being rendered: what it is, with no files yet, and where they will be -- its copies named,
        so a viewer that asked for one is not left thinking none is made."""
        where = " and ".join(str(path) for path in run.paths)
        copies = [f"its {preset} copy is being rendered with it" for preset in run.options.also]
        return {
            "id": run.id,
            "device": run.instance.name,
            "started_at": _when(run.started_at),
            "duration_ms": round(run.recorded_s / run.options.speed * 1000),
            "files": [],
            "notes": [
                f"still being rendered; it will be kept at {where}, and listed with the recordings once it is",
                *copies,
            ],
        }

    def finishing(self, config: SimConfig) -> list[RecordingFinishing]:
        """The recordings kept in the scope's folder that have stopped and are still being rendered, newest first."""
        folder = self._folder_for(config)
        return [run.finishing_state() for run in reversed(self._finishing.values()) if run.folder == folder]

    async def finish(self, run: RecordingRun) -> None:
        """Stop the recording's source and start keeping it. Finishing twice is finishing once.

        Keeping it begins at once, so a `stop` that comes while the source is still stopping has it to wait for; this
        returns once the source has stopped."""
        if run.finishing is not None:
            return
        if run.watchdog is not None and run.watchdog is not asyncio.current_task():
            run.watchdog.cancel()
        duration = self._clock() - run.began
        run.recorded_s = duration
        run.paths = tuple(final for final, _ in _planned(run))
        loop = asyncio.get_running_loop()
        stopped = loop.create_task(self._stop_source(run))
        run.finishing = loop.create_task(self._keep(run, duration, stopped))
        self._finishing[run.id] = run
        run.finishing.add_done_callback(lambda done: self._finished(run, done))
        with contextlib.suppress(Exception):
            await asyncio.shield(stopped)

    @staticmethod
    async def _stop_source(run: RecordingRun) -> DeviceControlError | None:
        """Stop the recording's source, and give the device its own status bar back: why the source failed, or None."""
        failure: DeviceControlError | None = None
        try:
            await run.source.stop()
        except DeviceControlError as exc:
            failure = exc
        if run.status_bar:
            await _own_status_bar(run.instance, run.control)
        return failure

    def _finished(self, run: RecordingRun, done: asyncio.Task[Recording]) -> None:
        """A recording is kept, or could not be: it is no longer finishing, and what went wrong is logged -- also when
        nobody waits for it, as when it stopped by itself or its device was let go."""
        self._finishing.pop(run.id, None)
        self._taken.discard((run.folder, run.id))
        if done.cancelled():
            return
        failure = done.exception()
        if failure is not None:
            logger.warning("recording %s on %s could not be kept: %s", run.id, run.instance.udid, failure)

    async def _keep(
        self, run: RecordingRun, duration: float, stopped: asyncio.Task[DeviceControlError | None]
    ) -> Recording:
        """Render the recording -- and each copy beside it -- keep it with its note, and let the oldest go."""
        failure = await stopped
        if failure is not None:
            _forget(run.source.raw)
            raise RecordingRefused(str(failure))
        try:
            rendered, notes = await self._render(run, duration)
        except (RecordingRefused, OSError) as exc:
            raise self._unrendered(run, duration, exc) from exc
        finally:
            _unfinished(run)
        try:
            copies, more = await self._copies(run, duration)
        finally:
            _unfinished(run)
            _forget(run.source.raw)
        files: list[RecordingFile] = [_file(made) for made in rendered]
        # A full-size movie too big for a pull request needs no advice when the copy beside it is the one for it.
        sized = [file for file in files if not (copies and file["format"] == "mp4")]
        notes = [*notes, *filter(None, (_too_big(file, run.options) for file in sized)), *more]
        for made, preset, options in copies:
            copy = _file(made)
            files.append(copy)
            notes += [
                f"{copy['name']} is the {preset} copy, beside the recording",
                *filter(None, [_too_big(copy, options)]),
            ]
        if run.by_itself:
            notes.append(f"it stopped by itself after {run.options.max_seconds}s, its limit")
        kept: Recording = {
            "id": run.id,
            "device": run.instance.name,
            "started_at": _when(run.started_at),
            "duration_ms": round(max((made.duration_s for made in rendered), default=duration) * 1000),
            "files": files,
            "notes": notes,
        }
        _note(run.folder / f"{run.id}.json", kept)
        self.prune(run.config)
        return kept

    def _unrendered(self, run: RecordingRun, duration: float, why: Exception) -> RecordingRefused:
        """A recording that could not be rendered is not lost: its raw recording is kept beside the others, and a note
        says why and where, so the list shows it. Answers what to raise."""
        raw = run.source.raw
        kept_at = run.folder / (f"{run.id}.raw.mp4" if run.source.kind == "movie" else f"{run.id}.raw")
        try:
            os.replace(raw, kept_at)
            os.chmod(kept_at, 0o600)
            where = f"the raw recording is kept at {kept_at}"
        except OSError as exc:
            where = f"its raw recording could not be kept either: {exc.strerror or exc}"
        said = f"{why}; {where}"
        unrendered: Recording = {
            "id": run.id,
            "device": run.instance.name,
            "started_at": _when(run.started_at),
            "duration_ms": round(duration * 1000),
            "files": [],
            "notes": [f"it could not be rendered: {said}"],
        }
        with contextlib.suppress(OSError):
            _note(run.folder / f"{run.id}.json", unrendered)
        _forget(raw)
        return RecordingRefused(said)

    async def _render(self, run: RecordingRun, duration: float) -> tuple[list[Rendered], list[str]]:
        options = run.options
        planned = {kind: final for final, kind in _planned(run) if kind in ("mp4", "gif")}
        mp4, gif = planned.get("mp4"), planned.get("gif")
        job = RenderJob(
            input=run.source.raw,
            input_kind=run.source.kind,
            touches=tuple(run.touches) if options.touches else (),
            mp4=None if mp4 is None else _partial(mp4),
            gif=None if gif is None else _partial(gif),
            codec=options.codec,
            speed=options.speed,
            gif_fps=options.gif_fps,
            gif_width=options.gif_width,
            mp4_width=options.mp4_width,
            mp4_quality=options.mp4_quality,
            duration_s=duration,
        )
        try:
            made = await self._renderer.render(job, run.config)
        except RecordingRefused as exc:
            if run.source.kind != "movie" or mp4 is None:
                raise
            # A simulator's own movie is kept as it was recorded when it cannot be rendered.
            os.replace(run.source.raw, mp4)
            said = f"kept as recorded, without its touches drawn or sped up: {exc}"
            return [Rendered(mp4, "mp4", 0, 0, duration)], [said]
        return [_renamed(file, planned.values()) for file in made], []

    async def _copies(
        self, run: RecordingRun, duration: float
    ) -> tuple[list[tuple[Rendered, str, RecordingOptions]], list[str]]:
        """Render each copy the recording's options ask for beside it, from the same recording. A copy that cannot be
        rendered is said, and leaves the rest as they are."""
        made: list[tuple[Rendered, str, RecordingOptions]] = []
        notes: list[str] = []
        for preset in run.options.also:
            options = run.options.as_preset(preset)
            if options == replace(run.options, also=()):
                notes.append(f"no {preset} copy: the recording itself is kept as {preset}")
                continue
            if not run.source.raw.exists():
                notes.append(f"no {preset} copy: the recording was kept as it was recorded")
                continue
            final = run.folder / f"{run.id}.{preset}.mp4"
            job = RenderJob(
                input=run.source.raw,
                input_kind=run.source.kind,
                touches=tuple(run.touches) if options.touches else (),
                mp4=_partial(final),
                gif=None,
                codec=options.codec,
                speed=options.speed,
                gif_fps=options.gif_fps,
                gif_width=options.gif_width,
                mp4_width=options.mp4_width,
                mp4_quality=options.mp4_quality,
                duration_s=duration,
            )
            try:
                made += [
                    (_renamed(file, (final,)), preset, options) for file in await self._renderer.render(job, run.config)
                ]
            except (RecordingRefused, OSError) as exc:
                notes.append(f"no {preset} copy: {exc}")
        return made, notes

    def prune(self, config: SimConfig) -> list[str]:
        """Let the oldest recordings go beyond ``recording.keep``. Answers the ids that went."""
        gone = self.listing(config)[config.recording_keep :]
        folder = self._folder_for(config)
        for recording in gone:
            for kept in recording["files"]:
                with contextlib.suppress(OSError):
                    (folder / kept["name"]).unlink()
            # A recording that could not be rendered has only its raw recording beside its note.
            for leftover in (f"{recording['id']}.raw", f"{recording['id']}.raw.mp4", f"{recording['id']}.json"):
                with contextlib.suppress(OSError):
                    (folder / leftover).unlink()
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


def _too_big(file: RecordingFile, options: RecordingOptions) -> str:
    """What to do about a file too big for GitHub to show inline, where a recording is most often put -- or "" when it
    is not too big."""
    faster = [speed for speed in SPEEDS if float(speed) > options.speed]
    how = f"speed {faster[-1]}" if faster else "a shorter take"
    size = f"{file['name']} is {file['bytes'] / 1_000_000:.1f} MB"
    if file["format"] == "gif" and file["bytes"] > GIF_INLINE_MAX:
        return (
            f"{size}, over the 10 MB GitHub shows inline: record again with {how} or a smaller recording.gif_width, "
            "or put the MP4 in a pull request instead"
        )
    if file["format"] == "mp4" and file["bytes"] > VIDEO_INLINE_MAX:
        pr = PRESETS["pr"]
        smallest = 0 < options.mp4_width <= pr["width"] and options.mp4_quality == pr["quality"]
        smaller = how if smallest else 'preset "pr" (450 wide, low quality)'
        return f"{size}, over the 10 MB a GitHub pull request takes on the free plan: record again with {smaller}"
    return ""


async def _own_status_bar(instance: DeviceInstance, control: StatusBar) -> None:
    try:
        await control.clear_status_bar(instance.udid)
    except DeviceControlError as exc:
        logger.warning("could not give %s its own status bar back: %s", instance.udid, exc)


def _when(started: datetime) -> str:
    return started.isoformat(timespec="seconds").replace("+00:00", "Z")


def _planned(run: RecordingRun) -> list[tuple[Path, str]]:
    """Where each of a recording's files will be kept, and what it is: ``mp4``, ``gif``, or the preset of a copy."""
    options, folder = run.options, run.folder
    planned = [(folder / f"{run.id}.{kind}", kind) for kind in ("mp4", "gif") if options.format in (kind, "both")]
    for preset in options.also:
        if options.as_preset(preset) != replace(options, also=()):
            planned.append((folder / f"{run.id}.{preset}.mp4", preset))
    return planned


def _partial(final: Path) -> Path:
    """Where a file is rendered before it is finished: hidden beside its final name, which no request serves."""
    return final.with_name(f".{final.stem}.{PARTIAL}{final.suffix}")


def _renamed(made: Rendered, finals: Iterable[Path]) -> Rendered:
    """A file the helper finished, given the name it is kept by."""
    for final in finals:
        if made.path == _partial(final):
            os.replace(made.path, final)
            return replace(made, path=final)
    return made


def _unfinished(run: RecordingRun) -> None:
    """Remove what a render that did not finish left half written."""
    for leftover in run.folder.glob(f".{run.id}.*{PARTIAL}*"):
        with contextlib.suppress(OSError):
            leftover.unlink()


def _note(path: Path, recording: Recording) -> None:
    """Write a recording's note whole, or not at all: the list never reads one half written."""
    write_atomic(path, json.dumps(recording, indent=1).encode("utf-8"), mode=0o600)


def _forget(raw: Path) -> None:
    for leftover in (raw, raw.with_suffix(".log")):
        with contextlib.suppress(OSError):
            leftover.unlink()
