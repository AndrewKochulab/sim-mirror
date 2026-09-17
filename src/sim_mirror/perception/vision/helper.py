# SPDX-License-Identifier: Apache-2.0
"""Running SimMirror's text reader: one helper for each Xcode a scope reads pixels with.

The helper (`text_recognizer.swift`) is compiled on first use with that Xcode (`platform.swift`), started, and spoken to
in JSON lines (`platform.json_lines`)::

    {"id": 1, "hello": true}
      -> {"id": 1, "version": 1, "languages": ["en-US", "fr-FR", …]}
    {"id": 2, "image": "<a JPEG, base64>", "level": "accurate", "languages": ["en-US"], "correction": true}
      -> {"id": 2, "lines": [{"text": "Sign in", "confidence": 1, "box": {"x": 0.25, "y": 0.24, "w": 0.19, "h": 0.03}}]}
      -> {"id": 2, "error": "the picture could not be decoded"}

A box is shares of the picture from its top left. Measured on macOS 26.6 with Xcode 26.6 (2026-09-17): the first hello
takes 0.4 seconds; an 804x1748 screenshot is read in 0.44 seconds at ``accurate`` and 0.05 at ``fast``, whose
confidence is always 0.5; an unknown language code is ignored rather than refused.

A helper is kept while it is used and ended after `IDLE_S` without a reading. One that stops is started again on the
next reading -- unless it stopped `STOPS_MAX` times within `STOPS_WINDOW_S`, when readings are refused with why
instead of starting it over and over. A build that failed is not tried again for `BUILD_RETRY_S`, so a Mac without
Swift does not pay for a compile on every snapshot.
"""

from __future__ import annotations

import asyncio
import base64
import math
import os
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from sim_mirror.perception.ocr import RecognitionOptions, RecognizedLine, TextBox, TextRecognitionError
from sim_mirror.platform.json_lines import JsonLines, Spawn, spawn_lines
from sim_mirror.platform.swift import SwiftBuildError, built_helper
from sim_mirror.platform.xcrun import XcrunRunner, run_xcrun

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

    async def recognize(self, jpeg: bytes, options: RecognitionOptions) -> tuple[RecognizedLine, ...]:
        helper = await self._ready(options.developer_dir)
        request = {
            "image": base64.b64encode(jpeg).decode("ascii"),
            "level": options.level,
            "languages": list(options.languages),
            "correction": options.correction,
        }
        answer = await helper.lines.request(request, timeout=options.timeout_s, what="a reading of the screen")
        self._keep(options.developer_dir, helper)
        return parse_lines(answer)

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
                helper = self._helpers[developer_dir] = _Helper(
                    JsonLines(HELPER, spawn=self._spawn, error=TextRecognitionError, answerer="the text reader")
                )
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
