# SPDX-License-Identifier: Apache-2.0
"""Running SimMirror's text reader: one helper for each Xcode a scope reads pixels with.

The helper (`text_recognizer.swift`) is compiled on first use with that Xcode (`platform.swift`), started, and spoken to
in JSON lines (`platform.json_lines`)::

    {"id": 1, "hello": true}
      -> {"id": 1, "version": 1, "languages": ["en-US", "fr-FR", …]}
    {"id": 2, "image": "<a JPEG, base64>", "level": "accurate", "languages": ["en-US"], "correction": true}
      -> {"id": 2, "lines": [{"text": "Sign in", "confidence": 1, "box": {"x": 0.25, "y": 0.24, "w": 0.19, "h": 0.03}}],
          "ms": 117}
      -> {"id": 2, "error": "the picture could not be decoded"}

A box is shares of the picture from its top left, and ``ms`` how long Vision itself took. Measured on macOS 26.6 with
Xcode 26.6 (2026-09-17): the first hello takes 0.4 seconds; an 804x1748 screenshot is read in 0.44 seconds at
``accurate`` and 0.05 at ``fast``, whose confidence is always 0.5; an unknown language code is ignored rather than
refused. The hello reads a word of its own first, so the first screen is not read cold: measured on macOS 26.6
(2026-10-26), a 1206x2622 screenshot's first reading takes 0.25 seconds after it rather than 0.57, the hello 0.3
seconds longer -- and a Mac with every core busy reads it in 0.2 seconds, not much slower.

A helper is kept while it is used and ended after `IDLE_S` without a reading. One that stops is started again on the
next reading -- unless it stopped `STOPS_MAX` times within `STOPS_WINDOW_S`, when readings are refused with why
instead of starting it over and over. A helper ended because a reading did not answer in time is started again on the
next one too, but is not counted as stopping: it did not crash, and a slow reading -- or a daemon too busy to hear the
answer -- would otherwise lock reading out for a minute. A reading that takes at least half the time it is given is
logged with how much of it Vision took, which tells a slow Vision from a busy daemon. A build that failed is not tried
again for `BUILD_RETRY_S`, so a Mac without Swift does not pay for a compile on every snapshot.

**A reading that did not answer in time is logged with what tells why** (`diagnosis`), in one line: whether the helper
was still running, how many readings were waiting on it, how long since it last answered, how big the picture was, and
how long the daemon's event loop was held meanwhile (`platform.json_lines.Silence`). Vision reads a screen in well under
a second, so a loop held most of the wait is a daemon too busy to hear the answer; a helper that last answered longer
ago than the wait, with readings queued behind, is one stuck on a picture.

How the reader stands for an Xcode -- running, stopped and why, not compiled and why, and its last slow reading, with
why when it did not answer -- is answered without starting or compiling anything (`VisionHelpers.state`), for a health
check to say.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import math
import os
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from PIL import Image

from sim_mirror.perception.ocr import RecognitionOptions, RecognizedLine, TextBox, TextRecognitionError
from sim_mirror.platform.json_lines import JsonLines, Silence, Spawn, spawn_lines
from sim_mirror.platform.swift import SwiftBuildError, built_helper
from sim_mirror.platform.xcrun import XcrunRunner, run_xcrun

logger = logging.getLogger(__name__)

HELPER = "sim-mirror-vision"
SOURCE = "text_recognizer.swift"
#: The version of what the helper is asked and answers, as `text_recognizer.swift` says hello with it.
VERSION = 1
#: How long the first hello may take: Vision loads its models.
HELLO_TIMEOUT_S = 30.0
#: How long a helper nobody reads with is kept.
IDLE_S = 300.0
STOPS_MAX = 3
STOPS_WINDOW_S = 60.0
BUILD_RETRY_S = 300.0
#: What a reading that did not answer in time adds: it was not a crash, and what gives readings longer.
SLOW = "it is started again for the next reading, and perception.ocr_timeout_ms gives a reading longer"


def helper_source() -> bytes:
    """The Swift the helper is compiled from, as this SimMirror ships it."""
    return resources.files(__package__).joinpath(SOURCE).read_bytes()


def _share(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return min(1.0, max(0.0, float(value)))


def _line(raw: Any) -> RecognizedLine | None:
    """One line of an answer, its numbers kept within the picture; None for one that is not a line of text."""
    if not isinstance(raw, dict) or not isinstance(raw.get("text"), str) or not raw["text"].strip():
        return None
    box = raw.get("box")
    if not isinstance(box, dict):
        return None
    parts = [_share(value) for value in (raw.get("confidence"), box.get("x"), box.get("y"), box.get("w"), box.get("h"))]
    numbers = [part for part in parts if part is not None]
    if len(numbers) != len(parts):
        return None
    confidence, x, y, width, height = numbers
    return RecognizedLine(raw["text"].strip(), confidence, TextBox(x, y, min(width, 1 - x), min(height, 1 - y)))


def parse_lines(answer: Mapping[str, Any]) -> tuple[RecognizedLine, ...]:
    """The lines of text a reading answered. Raises `TextRecognitionError` for an error or an answer that is none."""
    if "error" in answer:
        raise TextRecognitionError(f"Vision could not read the screen: {answer['error']}")
    lines = answer.get("lines")
    if not isinstance(lines, list):
        raise TextRecognitionError("the text reader answered without lines")
    return tuple(line for line in map(_line, lines) if line is not None)


def _log_if_slow(took: float, answer: Mapping[str, Any], options: RecognitionOptions) -> bool:
    """Log a reading that took at least half the time it is given, with how much of it Vision itself took. Whether it
    did."""
    if took < options.timeout_s / 2:
        return False
    ms = answer.get("ms")
    vision = f"{ms / 1000:.1f}s" if isinstance(ms, int) and not isinstance(ms, bool) else "an unknown share"
    logger.warning(
        "reading the screen took %.1fs of the %gs it is given, Vision %s of it (%s)",
        took,
        options.timeout_s,
        vision,
        options.level,
    )
    return True


def _picture(jpeg: bytes) -> str:
    """How big a picture is: in bytes, and in pixels when its header says."""
    size = f"{len(jpeg) / 1024:.0f} KB" if len(jpeg) >= 1024 else f"{len(jpeg)} bytes"
    try:
        with Image.open(io.BytesIO(jpeg)) as image:
            width, height = image.size
    except OSError:
        return size
    return f"{size} at {width}x{height} pixels"


def diagnosis(silence: Silence, jpeg: bytes) -> str:
    """What tells why a reading of `jpeg` did not answer in time, as a person reads it."""
    waiting = f"{silence.waiting} reading{' was' if silence.waiting == 1 else 's were'} waiting on it"
    since = silence.since_answer
    said = "it had not answered since it started" if since is None else f"it last answered {since:.1f}s before"
    return (
        f"it was {'still running' if silence.alive else 'not running'}, {waiting}, {said}, the picture was "
        f"{_picture(jpeg)}, and the event loop was held up to {silence.held:.1f}s meanwhile"
    )


@dataclass(frozen=True)
class SlowReading:
    """A reading that took at least half the time it was given -- or all of it -- and when, in seconds since the
    epoch: with what tells why, when it did not answer."""

    took: float
    given: float
    at: float
    why: str = ""

    def words(self) -> str:
        when = time.strftime("%H:%M:%S", time.localtime(self.at))
        if self.took >= self.given:
            why = f": {self.why}" if self.why else ""
            return f"its last slow reading did not answer within the {self.given:g}s it is given (at {when}){why}"
        return f"its last slow reading took {self.took:.1f}s of the {self.given:g}s it is given (at {when})"


@dataclass
class _Helper:
    """The helper for one Xcode: its process, what it said hello with, and when it stopped by itself."""

    lines: JsonLines
    languages: tuple[str, ...] = ()
    started: bool = False
    stops: deque[float] = field(default_factory=deque)
    idle: asyncio.TimerHandle | None = None


class VisionHelpers:
    """Reads text in pixels with SimMirror's helper, compiled and started for each Xcode on its first reading."""

    def __init__(
        self,
        *,
        folder: Path,
        xcrun: XcrunRunner = run_xcrun,
        spawn: Spawn = spawn_lines,
        source: Callable[[], bytes] = helper_source,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self._folder = folder
        self._xcrun = xcrun
        self._spawn = spawn
        self._source = source
        self._clock = clock
        self._helpers: dict[str, _Helper] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        #: A build that failed, by Xcode: when, and why.
        self._failed: dict[str, tuple[float, str]] = {}
        self._ending: set[asyncio.Task[None]] = set()
        self._wall = wall
        #: The last reading that was slow, by Xcode.
        self._slow: dict[str, SlowReading] = {}
        #: The last reading's silence logged: readings failed by its helper ending are not logged again.
        self._told: Silence | None = None

    async def recognize(self, jpeg: bytes, options: RecognitionOptions) -> tuple[RecognizedLine, ...]:
        helper = await self._ready(options.developer_dir)
        request = {
            "image": base64.b64encode(jpeg).decode("ascii"),
            "level": options.level,
            "languages": list(options.languages),
            "correction": options.correction,
        }
        started = self._clock()
        try:
            answer = await helper.lines.request(request, timeout=options.timeout_s, what="a reading of the screen")
        except TextRecognitionError as exc:
            if helper.lines.timed_out:
                # Said once, by the reading that timed out: not again by each reading its helper ending failed.
                silence = helper.lines.silence
                if silence is not None and silence is not self._told:
                    self._told, why = silence, diagnosis(silence, jpeg)
                    logger.warning("%s: %s", exc, why)
                    slow = SlowReading(options.timeout_s, options.timeout_s, self._wall(), why)
                    self._slow[options.developer_dir] = slow
                raise TextRecognitionError(f"{exc}: {SLOW}") from None
            raise
        self._keep(options.developer_dir, helper)
        took = self._clock() - started
        if _log_if_slow(took, answer, options):
            self._slow[options.developer_dir] = SlowReading(took, options.timeout_s, self._wall())
        return parse_lines(answer)

    def state(self, developer_dir: str) -> str:
        """How the reader for this Xcode stands, without starting or compiling it: not compiled and why, running, not
        running, stopped and why -- then its last slow reading, when one was."""
        now = self._clock()
        failed = self._failed.get(developer_dir)
        helper = self._helpers.get(developer_dir)
        if failed is not None and now - failed[0] < BUILD_RETRY_S:
            said = failed[1]
        elif helper is None:
            said = "not running; it starts on the next reading"
        elif helper.lines.alive:
            said = "running"
        elif sum(1 for stop in helper.stops if now - stop <= STOPS_WINDOW_S) >= STOPS_MAX:
            said = f"keeps stopping ({helper.lines.why_stopped()}); it is tried again after {STOPS_WINDOW_S:g} seconds"
        else:
            said = f"stopped ({helper.lines.why_stopped()}); it starts again on the next reading"
        slow = self._slow.get(developer_dir)
        return said if slow is None else f"{said}; {slow.words()}"

    async def languages(self, developer_dir: str) -> tuple[str, ...]:
        """The language codes the helper for this Xcode can read, starting it if it is not running."""
        helper = await self._ready(developer_dir)
        self._keep(developer_dir, helper)
        return helper.languages

    async def close(self) -> None:
        """End every helper, those already ending first."""
        for task in list(self._ending):
            await task
        for developer_dir in list(self._helpers):
            await self._end(developer_dir)

    async def _ready(self, developer_dir: str) -> _Helper:
        async with self._locks.setdefault(developer_dir, asyncio.Lock()):
            helper = self._helpers.get(developer_dir)
            if helper is None:
                lines = JsonLines(
                    HELPER, spawn=self._spawn, error=TextRecognitionError, answerer="the text reader", clock=self._clock
                )
                helper = self._helpers[developer_dir] = _Helper(lines)
            elif helper.lines.alive:
                return helper
            else:
                self._refuse_if_stopping(helper)
            binary = await self._binary(developer_dir)
            await helper.lines.start((str(binary),), dict(os.environ))
            helper.started = True
            hello = await helper.lines.request({"hello": True}, timeout=HELLO_TIMEOUT_S, what="hello")
            if hello.get("version") != VERSION:
                await helper.lines.close()
                raise TextRecognitionError(
                    f"the text reader said hello as version {hello.get('version')}, not {VERSION}: it is not this "
                    "SimMirror's"
                )
            said = hello.get("languages")
            helper.languages = tuple(code for code in said if isinstance(code, str)) if isinstance(said, list) else ()
            return helper

    def _refuse_if_stopping(self, helper: _Helper) -> None:
        """Count the stop of a helper that was running, once; refuse to start one that keeps stopping."""
        now = self._clock()
        if helper.started:
            helper.started = False
            if not helper.lines.timed_out:
                helper.stops.append(now)
        while helper.stops and now - helper.stops[0] > STOPS_WINDOW_S:
            helper.stops.popleft()
        if len(helper.stops) >= STOPS_MAX:
            raise TextRecognitionError(
                f"the text reader keeps stopping ({helper.lines.why_stopped()}); it is tried again after "
                f"{STOPS_WINDOW_S:g} seconds"
            )

    async def _binary(self, developer_dir: str) -> Path:
        failed = self._failed.get(developer_dir)
        if failed is not None and self._clock() - failed[0] < BUILD_RETRY_S:
            raise TextRecognitionError(failed[1])
        try:
            binary = await built_helper(
                self._source(), name=HELPER, folder=self._folder, developer_dir=developer_dir, xcrun=self._xcrun
            )
        except SwiftBuildError as exc:
            why = f"the text reader could not be compiled: {exc}"
            self._failed[developer_dir] = (self._clock(), why)
            raise TextRecognitionError(why) from None
        self._failed.pop(developer_dir, None)
        return binary

    def _keep(self, developer_dir: str, helper: _Helper) -> None:
        """A reading just used the helper: it is ended `IDLE_S` after the last one."""
        if helper.idle is not None:
            helper.idle.cancel()
        loop = asyncio.get_running_loop()
        helper.idle = loop.call_later(IDLE_S, self._end_idle, developer_dir, helper)

    def _end_idle(self, developer_dir: str, helper: _Helper) -> None:
        if self._helpers.get(developer_dir) is not helper:
            return
        task = asyncio.get_running_loop().create_task(self._end(developer_dir))
        self._ending.add(task)
        task.add_done_callback(self._ending.discard)

    async def _end(self, developer_dir: str) -> None:
        helper = self._helpers.pop(developer_dir, None)
        if helper is None:
            return
        if helper.idle is not None:
            helper.idle.cancel()
        await helper.lines.close()
