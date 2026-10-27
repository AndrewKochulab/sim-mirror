# SPDX-License-Identifier: Apache-2.0
"""Text read from pixels: lines as text elements where they are, a screen that did not change read once, lines the
reading is unsure of left out, and a device's readings let go when it ends."""

from __future__ import annotations

import asyncio
from io import BytesIO

import pytest
from PIL import Image

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import ConnectorError, Screen, Shot
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
    on_device,
    tree_from_lines,
)
from sim_mirror.perception.orientation import Interface, Orientation
from sim_mirror.perception.snapshot import build
from sim_mirror.testing.fakes import SCREEN, FakeEngine
from sim_mirror.testing.pictures import picture
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


def rounded(frame: Frame | None) -> tuple[float, ...]:
    assert frame is not None
    return tuple(round(value, 6) for value in (frame.x, frame.y, frame.width, frame.height))


def known(orientation: Orientation) -> asyncio.Future[Orientation]:
    told: asyncio.Future[Orientation] = asyncio.get_running_loop().create_future()
    told.set_result(orientation)
    return told


def corner(jpeg: bytes) -> tuple[tuple[int, int], bool]:
    """A picture's size, and whether its top left corner is the dark one."""
    with Image.open(BytesIO(jpeg)) as image:
        return image.size, bool(image.convert("L").getpixel((2, 2)) < 128)  # type: ignore[operator]


#: An app turned landscape right, its top left corner -- dark -- at the portrait screen's top right.
TURNED_RIGHT = picture(80, 174, boxes=[(72, 0, 8, 8, 0)])
#: A line read in the app's 874x402 points.
HIGH_SCORE = text_line("High score", 100, 40, 200, 30, screen=Interface(SCREEN, "landscape_right").screen)


async def test_a_turned_apps_screenshot_is_read_upright_and_its_lines_are_in_the_apps_points() -> None:
    recognizer = FakeTextRecognizer([HIGH_SCORE])
    told: list[tuple[RecognizedLine, ...]] = []
    reader = OcrReaders(recognizer, supported=True).reader("U", Screens(TURNED_RIGHT), SCREEN, CONFIG, told.append)
    assert reader is not None
    tree = await reader.read_in(known("landscape_right"))
    # Before, the portrait picture was read, its text on its side.
    [(read, _)] = recognizer.readings
    assert corner(read) == ((174, 80), True)
    (node,) = tree.roots
    assert tree.orientation == "landscape_right" and rounded(node.frame) == (100, 40, 200, 30)
    # A viewer draws over the device's portrait screen: the line is there, down its right side.
    [(line,)] = told
    assert rounded(frame_of(line.box, SCREEN)) == (332, 100, 30, 200)
    await reader.read_in(known("landscape_right"))
    await reader.read()
    # The same screen read the same way again is not read again; read as it lies, it is.
    assert [corner(jpeg) for jpeg, _ in recognizer.readings] == [((174, 80), True), ((80, 174), False)]


class Pictured(FakeEngine):
    """A device whose screenshot is a picture `across` by `down` pixels, whatever is asked."""

    def __init__(self, across: int, down: int) -> None:
        super().__init__()
        self.size = across, down

    async def screenshot(self, *, max_width: int, quality: int, crop: object = None) -> Shot:
        await super().screenshot(max_width=max_width, quality=quality)
        return Shot(b"pictured", *self.size)


async def test_a_picture_of_the_screen_turned_a_quarter_is_read_in_its_own_shape() -> None:
    """simctl's screenshot turns with the device: measured, an iPhone 17 Pro turned after it was described came out
    2622x1206, shrunk to 804x370 for reading. Its lines are where they are in that picture, on a screen of its shape."""
    turned = Screen(2622, 1206, 874, 402, 3.0)
    line = text_line("Don't Allow", 300, 240, 120, 30, screen=turned)
    recognizer = FakeTextRecognizer([line])
    told: list[tuple[RecognizedLine, ...]] = []
    reader = OcrReaders(recognizer, supported=True).reader("U", Pictured(804, 370), SCREEN, CONFIG, told.append)
    assert reader is not None
    tree = await reader.read()
    # Before, its shares were taken of the portrait screen described: the line at (130, 525), 55 wide.
    (node,) = tree.roots
    assert tree.screen == turned and rounded(node.frame) == (300, 240, 120, 30)
    header, first = build(tree, device="iOS 26.5", screen=SCREEN, max_elements=10).text().splitlines()[:2]
    assert header.startswith("iOS 26.5 · 874x402pt · #") and first == 'e1 text "Don\'t Allow" (360,255)'
    # A viewer shows that picture: the line is outlined where it was read.
    assert told == [(line,)]
    # A screen already described turned, shown so: as it is.
    again = OcrReaders(recognizer, supported=True).reader("U", Pictured(804, 370), turned, CONFIG)
    assert again is not None and (await again.read()).screen == turned


async def test_the_framebuffer_of_a_turned_app_is_turned_upright_whatever_its_shape() -> None:
    """The native helper's and idb's screenshot is the portrait framebuffer: a picture shaped otherwise with an
    interface known to be turned is still the device's, turned upright before it is read."""
    recognizer = FakeTextRecognizer([HIGH_SCORE])
    reader = OcrReaders(recognizer, supported=True).reader("U", Pictured(804, 370), SCREEN, CONFIG)
    assert reader is not None
    tree = await reader.read_in(known("landscape_right"))
    assert tree.screen == SCREEN and tree.orientation == "landscape_right"


@pytest.mark.parametrize(
    ("orientation", "where"),
    [
        ("portrait", (100, 40, 200, 30)),
        ("landscape_right", (332, 100, 30, 200)),
        ("landscape_left", (40, 574, 30, 200)),
        ("portrait_upside_down", (102, 804, 200, 30)),
    ],
)
def test_a_line_of_a_turned_interface_is_put_back_where_it_is_on_the_devices_screen(
    orientation: Orientation, where: tuple[float, ...]
) -> None:
    interface = Interface(SCREEN, orientation)
    line = text_line("High score", 100, 40, 200, 30, screen=interface.screen)
    placed = on_device(line, interface)
    assert rounded(frame_of(placed.box, SCREEN)) == where and placed.text == line.text


class Says(FakeTextRecognizer):
    """A recognizer that says how it stands for each Xcode."""

    def state(self, developer_dir: str) -> str:
        return f"running for {developer_dir}"


def test_how_the_text_reader_stands_is_said_for_a_scope_without_starting_it() -> None:
    config = SimConfig.defaults().with_values(developer_dir="/Applications/Xcode.app/Contents/Developer")
    assert OcrReaders(NoRecognizer(), supported=False).state(config).startswith("not available")
    assert OcrReaders(Says(), supported=True).state(config.with_values(ocr_mode="off")) == "off (perception.ocr)"
    assert OcrReaders(Says(), supported=True).state(config) == "running for /Applications/Xcode.app/Contents/Developer"
    assert OcrReaders(FakeTextRecognizer(), supported=True).state(config) == "ready", "one that cannot say is ready"
