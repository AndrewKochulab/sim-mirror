# SPDX-License-Identifier: Apache-2.0
"""Text read from pixels: lines as text elements where they are, a screen that did not change read once, lines the
reading is unsure of left out, and a device's readings let go when it ends."""

from __future__ import annotations

import pytest

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import ConnectorError, Shot
from sim_mirror.perception.model import PIXELS, Frame
from sim_mirror.perception.ocr import (
    CACHE_SIZE,
    OCR_QUALITY,
    NoRecognizer,
    OcrReaders,
    RecognitionCache,
    RecognitionOptions,
    RecognizedLine,
    TextBox,
    TextRecognitionError,
    frame_of,
    tree_from_lines,
)
from sim_mirror.perception.snapshot import build
from sim_mirror.testing.fakes import SCREEN, FakeEngine
from sim_mirror.testing.vision import FakeTextRecognizer, text_line

CONFIG = SimConfig.defaults()
SIGN_IN = text_line("Sign in", 150, 400, 100, 20)
FAINT = text_line("Terms", 20, 840, 60, 12, confidence=0.2)


def test_options_are_the_scopes_and_two_readings_are_alike_whatever_their_xcode_or_time() -> None:
    config = CONFIG.with_values(ocr_level="fast", ocr_languages="en-US, uk-UA", ocr_correction=False,
                                ocr_timeout_ms=1500, developer_dir="/X.app/Contents/Developer")  # fmt: skip
    options = RecognitionOptions.from_config(config)
    assert options == RecognitionOptions("/X.app/Contents/Developer", "fast", ("en-US", "uk-UA"), False, 1.5)
    assert options.reads_alike() == RecognitionOptions("", "fast", ("en-US", "uk-UA"), False, 9).reads_alike()
    assert RecognitionOptions.from_config(CONFIG) == RecognitionOptions("", "accurate", (), True, 5.0)


def test_a_line_is_a_text_element_where_it_is_on_screen() -> None:
    assert frame_of(TextBox(0.5, 0.25, 0.1, 0.02), SCREEN) == Frame(201.0, 218.5, 40.2, 17.48)
    tree = tree_from_lines([SIGN_IN], SCREEN)
    (node,) = tree.roots
    assert tree.pixels is True and node.role == "StaticText" and node.label == "Sign in" and node.source == PIXELS
    assert node.frame == pytest.approx(Frame(150, 400, 100, 20))
    assert tree_from_lines([], SCREEN).roots == () and tree_from_lines([], SCREEN).pixels is True


class Screens(FakeEngine):
    """A device whose screenshots are `pictures` in turn, the last one held."""

    def __init__(self, *pictures: bytes) -> None:
        super().__init__()
        self.pictures = list(pictures)

    async def screenshot(self, *, max_width: int, quality: int, crop: object = None) -> Shot:
        await super().screenshot(max_width=max_width, quality=quality)
        picture = self.pictures.pop(0) if len(self.pictures) > 1 else self.pictures[0]
        return Shot(picture, max_width, 100)


async def test_a_reader_reads_a_screenshot_at_two_pixels_a_point_and_keeps_what_it_is_sure_enough_of() -> None:
    recognizer = FakeTextRecognizer([SIGN_IN, FAINT])
    readers = OcrReaders(recognizer, supported=True)
    engine = Screens(b"login")
    told: list[tuple[RecognizedLine, ...]] = []
    reader = readers.reader("U", engine, SCREEN, CONFIG, on_read=told.append)
    assert reader is not None
    tree = await reader.read()
    assert [node.label for node in tree.roots] == ["Sign in"] and told == [(SIGN_IN,)]
    assert engine.screenshots == [(804, OCR_QUALITY, None)]
    assert recognizer.readings == [(b"login", RecognitionOptions.from_config(CONFIG))]
    unsure = readers.reader("U", engine, SCREEN, CONFIG.with_values(ocr_min_confidence=10))
    assert unsure is not None and [node.label for node in (await unsure.read()).roots] == ["Sign in", "Terms"]
    assert len(recognizer.readings) == 1


async def test_a_screen_that_did_not_change_is_read_once_until_how_it_is_read_changes() -> None:
    recognizer = FakeTextRecognizer([SIGN_IN])
    readers = OcrReaders(recognizer, supported=True)
    engine = Screens(b"login", b"login", b"login", b"home")
    for config in (CONFIG, CONFIG, CONFIG.with_values(ocr_level="fast"), CONFIG):
        reader = readers.reader("U", engine, SCREEN, config)
        assert reader is not None
        await reader.read()
    assert [(jpeg, options.level) for jpeg, options in recognizer.readings] == [
        (b"login", "accurate"), (b"login", "fast"), (b"home", "accurate"),
    ]  # fmt: skip


def test_the_cache_keeps_the_most_recently_used_readings() -> None:
    options = RecognitionOptions.from_config(CONFIG)
    cache = RecognitionCache(size=2)
    cache.put(b"a", options, (SIGN_IN,))
    cache.put(b"b", options, ())
    assert cache.get(b"a", options) == (SIGN_IN,)
    cache.put(b"c", options, ())
    assert cache.get(b"b", options) is None and cache.get(b"z", options) is None
    assert cache.get(b"a", options) == (SIGN_IN,) and RecognitionCache()._size == CACHE_SIZE


async def test_a_reading_or_a_screenshot_that_fails_is_the_readers_failure_and_nothing_is_kept() -> None:
    recognizer = FakeTextRecognizer([SIGN_IN])
    recognizer.errors = [TextRecognitionError("Vision could not read the screen: no")]
    readers = OcrReaders(recognizer, supported=True)
    engine = Screens(b"login")
    reader = readers.reader("U", engine, SCREEN, CONFIG)
    assert reader is not None
    with pytest.raises(TextRecognitionError, match="Vision could not read the screen"):
        await reader.read()
    assert [node.label for node in (await reader.read()).roots] == ["Sign in"]
    engine.screenshot_errors = [ConnectorError("taking a screenshot failed")]
    with pytest.raises(ConnectorError, match="taking a screenshot failed"):
        await reader.read()


async def test_readers_are_offered_only_where_a_scope_reads_pixels_on_a_mac_and_let_go_when_a_device_ends() -> None:
    recognizer = FakeTextRecognizer([SIGN_IN])
    readers = OcrReaders(recognizer, supported=True)
    engine = Screens(b"login")
    assert readers.enabled(CONFIG) and not readers.enabled(CONFIG.with_values(ocr_mode="off"))
    assert readers.reader("U", engine, SCREEN, CONFIG.with_values(ocr_mode="off")) is None
    elsewhere = OcrReaders(recognizer, supported=False)
    assert not elsewhere.enabled(CONFIG) and elsewhere.reader("U", engine, SCREEN, CONFIG) is None
    for _ in range(2):
        reader = readers.reader("U", engine, SCREEN, CONFIG)
        assert reader is not None
        await reader.read()
        readers.forget("U")
    readers.forget("never seen")
    assert len(recognizer.readings) == 2
    await readers.close()
    assert recognizer.closed


async def test_where_no_text_reader_runs_reading_says_so() -> None:
    recognizer = NoRecognizer()
    with pytest.raises(TextRecognitionError, match="can only be read on a Mac"):
        await recognizer.recognize(b"", RecognitionOptions.from_config(CONFIG))
    await recognizer.close()


def test_lines_read_from_pixels_snapshot_as_text_with_refs_and_say_so() -> None:
    tree = tree_from_lines([SIGN_IN, text_line("Forgot password?", 120, 460, 160, 20)], SCREEN)
    snapshot = build(tree, device="iOS 26.5", screen=SCREEN, max_elements=10)
    assert snapshot.text().splitlines()[1:] == [
        'e1 text "Sign in" (200,410)',
        'e2 text "Forgot password?" (200,470)',
        "2 lines were read from the screen's pixels: text may be misread, and a ref taps its middle",
    ]
