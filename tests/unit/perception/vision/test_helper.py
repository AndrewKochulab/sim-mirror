# SPDX-License-Identifier: Apache-2.0
"""SimMirror's text reader: compiled and started once per Xcode, asked in JSON lines, answers checked, and a helper that
cannot be built or keeps stopping refused with why rather than tried on every snapshot."""

from __future__ import annotations

import asyncio
import base64
import math
from pathlib import Path
from typing import Any

import pytest

from sim_mirror.perception.ocr import RecognitionOptions, RecognizedLine, TextBox, TextRecognitionError
from sim_mirror.perception.vision import helper as helper_module
from sim_mirror.perception.vision.helper import (
    BUILD_RETRY_S,
    HELPER,
    STOPS_WINDOW_S,
    VisionHelpers,
    helper_source,
    parse_lines,
)
from sim_mirror.platform.swift import build_key
from sim_mirror.testing.fakes import SWIFT_VERSION, FakeXcrun, ManualClock
from sim_mirror.testing.vision import LANGUAGES, FakeVisionHelper

XCODE = "/Applications/Xcode.app/Contents/Developer"
XCODE_27 = "/Applications/Xcode27.app/Contents/Developer"
SIGN_IN = {"text": "Sign in", "confidence": 1, "box": {"x": 0.25, "y": 0.24, "w": 0.19, "h": 0.03}}
OPTIONS = RecognitionOptions(XCODE, "accurate", ("en-US", "uk-UA"), True, 5.0)


class Rig:
    def __init__(self, folder: Path, *, lines: tuple[dict[str, Any], ...] = (SIGN_IN,)) -> None:
        self.folder = folder
        self.xcrun = FakeXcrun().with_swift()
        self.helper = FakeVisionHelper(lines)
        self.clock = ManualClock()
        self.vision = VisionHelpers(folder=folder, xcrun=self.xcrun, spawn=self.helper.spawn,
                                    source=lambda: b"// the reader", clock=self.clock)  # fmt: skip

    def compiles(self) -> int:
        return sum(1 for call in self.xcrun.calls if "-O" in call.args)


async def test_the_first_reading_compiles_and_starts_the_helper_and_later_ones_use_it(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    first = await rig.vision.recognize(b"picture", OPTIONS)
    second = await rig.vision.recognize(b"another", OPTIONS)
    assert first == second == (RecognizedLine("Sign in", 1.0, TextBox(0.25, 0.24, 0.19, 0.03)),)
    (process,) = rig.helper.processes
    assert process.argv == (str(tmp_path / build_key(b"// the reader", SWIFT_VERSION) / HELPER),)
    assert rig.compiles() == 1 and rig.xcrun.calls[-1].developer_dir == XCODE
    hello, reading = rig.helper.heard[0], rig.helper.readings()[0]
    assert hello == {"hello": True, "id": 1}
    assert reading == {"image": base64.b64encode(b"picture").decode(), "level": "accurate",
                       "languages": ["en-US", "uk-UA"], "correction": True, "id": 2}  # fmt: skip
    assert await rig.vision.languages(XCODE) == LANGUAGES
    await rig.vision.close()
    assert process.returncode is not None


async def test_each_xcode_has_a_helper_of_its_own_built_once_for_the_same_swift(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    await rig.vision.recognize(b"picture", OPTIONS)
    await rig.vision.recognize(b"picture", RecognitionOptions(XCODE_27, "fast", (), False, 5.0))
    assert len(rig.helper.processes) == 2 and rig.helper.readings()[1]["level"] == "fast"
    asked = [call.developer_dir for call in rig.xcrun.calls if "--version" in call.args]
    assert asked == [XCODE, XCODE_27] and rig.compiles() == 1
    await rig.vision.close()
    assert all(process.returncode is not None for process in rig.helper.processes)


def test_an_answer_is_read_as_lines_kept_within_the_picture_and_what_is_not_a_line_is_left_out() -> None:
    answer = {
        "lines": [
            SIGN_IN,
            {"text": "  Edge  ", "confidence": 1.4, "box": {"x": 0.9, "y": -0.1, "w": 0.5, "h": 2}},
            {"text": " ", "confidence": 1, "box": SIGN_IN["box"]},
            {"text": 7, "confidence": 1, "box": SIGN_IN["box"]},
            {"text": "no box", "confidence": 1},
            {"text": "flag", "confidence": True, "box": SIGN_IN["box"]},
            {"text": "nan", "confidence": 1, "box": {**SIGN_IN["box"], "w": math.nan}},
            {"text": "word", "confidence": 1, "box": {**SIGN_IN["box"], "h": "tall"}},
            "not a line",
        ]
    }
    assert parse_lines(answer) == (
        RecognizedLine("Sign in", 1.0, TextBox(0.25, 0.24, 0.19, 0.03)),
        RecognizedLine("Edge", 1.0, TextBox(0.9, 0.0, pytest.approx(0.1), 1.0)),  # type: ignore[arg-type]
    )
    with pytest.raises(TextRecognitionError, match="Vision could not read the screen: the picture could not be"):
        parse_lines({"id": 2, "error": "the picture could not be decoded"})
    with pytest.raises(TextRecognitionError, match="the text reader answered without lines"):
        parse_lines({"id": 2, "lines": "none"})


async def test_a_reading_the_helper_refuses_or_does_not_answer_in_time_is_said(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.helper.error = "the picture could not be decoded"
    with pytest.raises(TextRecognitionError, match="Vision could not read the screen"):
        await rig.vision.recognize(b"picture", OPTIONS)
    rig.helper.error, rig.helper.quiet = "", True
    fast = RecognitionOptions(XCODE, "fast", (), True, 0.05)
    with pytest.raises(
        TextRecognitionError, match=r"the text reader did not answer a reading of the screen within 0\.05"
    ):
        await rig.vision.recognize(b"picture", fast)
    rig.helper.quiet = False
    assert await rig.vision.recognize(b"picture", OPTIONS)
    assert len(rig.helper.processes) == 2 and rig.compiles() == 1
    await rig.vision.close()


async def test_a_helper_of_another_version_is_not_used(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.helper.version = 2
    with pytest.raises(TextRecognitionError, match="said hello as version 2, not 1: it is not this SimMirror's"):
        await rig.vision.recognize(b"picture", OPTIONS)
    assert rig.helper.processes[0].returncode is not None
    rig.helper.version = 1
    assert await rig.vision.recognize(b"picture", OPTIONS)
    await rig.vision.close()


async def test_a_helper_that_keeps_stopping_is_refused_until_a_while_has_passed(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    for _ in range(2):
        await rig.vision.recognize(b"picture", OPTIONS)
        rig.helper.stop_saying = "Fatal error: out of memory"
        with pytest.raises(TextRecognitionError, match="sim-mirror-vision stopped: Fatal error: out of memory"):
            await rig.vision.recognize(b"picture", OPTIONS)
    await rig.vision.recognize(b"picture", OPTIONS)
    rig.helper.stop_saying = "Fatal error: out of memory"
    with pytest.raises(TextRecognitionError):
        await rig.vision.recognize(b"picture", OPTIONS)
    for _ in range(2):
        with pytest.raises(TextRecognitionError, match=r"keeps stopping \(sim-mirror-vision stopped: Fatal error"):
            await rig.vision.recognize(b"picture", OPTIONS)
    assert len(rig.helper.processes) == 3
    rig.clock.advance(STOPS_WINDOW_S + 1)
    assert await rig.vision.recognize(b"picture", OPTIONS)
    assert len(rig.helper.processes) == 4
    await rig.vision.close()


async def test_a_helper_that_cannot_be_compiled_is_not_compiled_again_for_a_while(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.xcrun.with_swift(compiles=False)
    for _ in range(2):
        with pytest.raises(TextRecognitionError, match="the text reader could not be compiled: compiling sim-mirror"):
            await rig.vision.recognize(b"picture", OPTIONS)
    assert rig.compiles() == 1 and rig.helper.processes == []
    await rig.vision.close()
    rig.xcrun.with_swift()
    rig.clock.advance(BUILD_RETRY_S)
    assert await rig.vision.recognize(b"picture", OPTIONS)
    assert rig.compiles() == 2
    await rig.vision.close()


async def test_a_helper_nobody_reads_with_is_ended_and_started_again_when_needed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(helper_module, "IDLE_S", 0.01)
    rig = Rig(tmp_path)
    await rig.vision.recognize(b"picture", OPTIONS)
    await rig.vision.recognize(b"picture", OPTIONS)
    await asyncio.sleep(0.05)
    assert rig.helper.processes[0].returncode is not None
    assert await rig.vision.recognize(b"picture", OPTIONS)
    assert len(rig.helper.processes) == 2 and rig.compiles() == 1
    helper = rig.vision._helpers[XCODE]
    rig.vision._end_idle(XCODE_27, helper)
    rig.vision._end_idle(XCODE, helper)
    await rig.vision.close()
    await rig.vision._end(XCODE)
    assert rig.helper.processes[1].returncode is not None


async def test_a_hello_without_a_list_of_languages_reads_as_none(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    def hello(process: Any, message: dict[str, Any]) -> None:
        process.say({"id": message["id"], "version": 1, "languages": "all"})

    rig.helper.receive = hello  # type: ignore[method-assign]
    assert await rig.vision.languages(XCODE) == ()
    await rig.vision.close()


def test_the_helper_ships_as_swift_that_answers_what_it_is_asked() -> None:
    source = helper_source().decode()
    assert source.startswith("// SPDX-License-Identifier: Apache-2.0")
    for key in ("hello", "image", "level", "languages", "correction", "version", "lines", "text", "confidence",
                "box", "error"):  # fmt: skip
        assert f"let {key}" in source or f"var {key}" in source, key
    assert "let protocolVersion = 1" in source and helper_module.VERSION == 1
