# SPDX-License-Identifier: Apache-2.0
"""Reading text in pixels without Vision: a text recognizer that reads what it is told to, and SimMirror's Swift text
reader played over JSON lines, answering as the real one does."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any

from sim_mirror.connectors.base import Screen
from sim_mirror.perception.ocr import RecognitionOptions, RecognizedLine, TextBox
from sim_mirror.testing.fakes import SCREEN, FakeLineProcess


def text_line(
    text: str, x: float, y: float, width: float, height: float, *, confidence: float = 0.95, screen: Screen = SCREEN
) -> RecognizedLine:
    """A line of text read where these points are on `screen`."""
    w, h = float(screen.width_pt), float(screen.height_pt)
    return RecognizedLine(text, confidence, TextBox(x / w, y / h, width / w, height / h))


class FakeTextRecognizer:
    """Reads `lines` in any picture; raises `errors`, one per reading, first."""

    def __init__(self, lines: Sequence[RecognizedLine] = ()) -> None:
        self.lines = tuple(lines)
        self.errors: list[Exception] = []
        self.readings: list[tuple[bytes, RecognitionOptions]] = []
        self.closed = False

    async def recognize(self, jpeg: bytes, options: RecognitionOptions) -> tuple[RecognizedLine, ...]:
        self.readings.append((jpeg, options))
        await asyncio.sleep(0)
        if self.errors:
            raise self.errors.pop(0)
        return self.lines

    async def close(self) -> None:
        self.closed = True


#: The languages Vision on macOS 26 read at the accurate level, as the helper says hello with them.
LANGUAGES = ("en-US", "fr-FR", "it-IT", "de-DE", "es-ES", "pt-BR", "zh-Hans", "zh-Hant", "ja-JP", "ko-KR", "uk-UA")


class FakeVisionHelper:
    """SimMirror's text reader behind `spawn`: says hello as version `version`, and reads `lines` in every picture --
    or answers `error`, or nothing while `quiet`."""

    def __init__(self, lines: Sequence[Mapping[str, Any]] = (), *, version: int = 1) -> None:
        self.lines = [dict(line) for line in lines]
        self.version = version
        self.error = ""
        self.quiet = False
        self.exit_on_close = True
        #: Stopped on its next message, saying this on stderr.
        self.stop_saying: str | None = None
        self.processes: list[FakeLineProcess] = []
        self.heard: list[dict[str, Any]] = []

    async def spawn(self, argv: Sequence[str], env: Mapping[str, str]) -> FakeLineProcess:
        process = FakeLineProcess(self, tuple(argv), dict(env))
        self.processes.append(process)
        return process

    def receive(self, process: FakeLineProcess, message: dict[str, Any]) -> None:
        self.heard.append(message)
        if self.stop_saying is not None:
            said, self.stop_saying = self.stop_saying, None
            process.finish(1, said)
        elif message.get("hello"):
            process.say({"id": message["id"], "version": self.version, "languages": list(LANGUAGES)})
        elif self.quiet:
            return
        elif self.error:
            process.say({"id": message["id"], "error": self.error})
        else:
            process.say({"id": message["id"], "lines": self.lines})

    def readings(self) -> list[dict[str, Any]]:
        return [message for message in self.heard if "image" in message]
