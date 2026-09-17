# SPDX-License-Identifier: Apache-2.0
"""Text read from a screen's pixels, as elements with a place: the reader for what accessibility does not say.

A game, a canvas, an app still loading, an app whose accessibility stopped answering, or a device whose connector cannot
read a tree at all: each still shows text. An `OcrReader` takes a screenshot, has a `TextRecognizer` read the lines of
text in it -- macOS's Vision, in SimMirror's own helper (`perception.vision`) -- and answers them as a `ScreenTree` of
text elements whose frames are where the text is, so an agent can find ``Sign in`` and tap its ref. Their source is
`PIXELS`, so a snapshot can say which lines were read this way.

What is recognised is kept per device for a few screenshots (`RecognitionCache`): a screen that did not change is not
read again, and reads the same -- the same text, the same refs. Lines the reader is less sure of than the scope's
``perception.ocr_min_confidence`` are left out after the cache, so changing it needs no new reading.

`OcrReaders` answers, per device, whether its scope reads pixels at all (``perception.ocr`` and a Mac to read them on)
and the reader to read them with.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from sim_mirror.config.model import SimConfig
from sim_mirror.config.schema import language_codes
from sim_mirror.connectors.base import ConnectorError, Screen, ScreenSource
from sim_mirror.perception.model import PIXELS, ElementNode, Frame, ScreenTree

#: How many screenshot pixels a point is read at: enough for Vision to read small text, at most the screen's own.
PIXELS_PER_POINT = 2
#: The JPEG quality of the screenshot read: high, since compression blurs the edges of letters.
OCR_QUALITY = 85
#: How many screenshots' readings a device keeps.
CACHE_SIZE = 8


class TextRecognitionError(ConnectorError):
    """Text could not be read from the screen's pixels."""


@dataclass(frozen=True)
class RecognitionOptions:
    """How a screenshot is read: with which Xcode's helper, how carefully, in which languages, and for how long."""

    developer_dir: str
    level: Literal["accurate", "fast"]
    languages: tuple[str, ...]
    correction: bool
    timeout_s: float

    @classmethod
    def from_config(cls, config: SimConfig) -> RecognitionOptions:
        return cls(
            developer_dir=config.developer_dir,
            level=config.ocr_level,
            languages=language_codes(config.ocr_languages),
            correction=config.ocr_correction,
            timeout_s=config.ocr_timeout_ms / 1000,
        )

    def reads_alike(self) -> tuple[object, ...]:
        """What makes two readings of the same picture the same: not the Xcode, nor how long one may take."""
        return self.level, self.languages, self.correction


@dataclass(frozen=True)
class TextBox:
    """Where a line of text is, as shares of the screenshot's width and height from its top left."""

    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class RecognizedLine:
    text: str
    #: How sure the reading is, from 0 to 1.
    confidence: float
    box: TextBox


class TextRecognizer(Protocol):
    async def recognize(self, jpeg: bytes, options: RecognitionOptions) -> tuple[RecognizedLine, ...]:
        """Every line of text in a JPEG, however unsure. Raises `TextRecognitionError`."""
        ...

    async def close(self) -> None:
        """Let go of whatever reads text."""
        ...


class NoRecognizer:
    """Reads nothing: for where no text reader can run."""

    async def recognize(self, jpeg: bytes, options: RecognitionOptions) -> tuple[RecognizedLine, ...]:
        raise TextRecognitionError("text in the screen's pixels can only be read on a Mac")

    async def close(self) -> None:
        return None


def frame_of(box: TextBox, screen: Screen) -> Frame:
    """A text box's place on the screen, in points."""
    width, height = float(screen.width_pt), float(screen.height_pt)
    return Frame(box.x * width, box.y * height, box.width * width, box.height * height)


def tree_from_lines(lines: Sequence[RecognizedLine], screen: Screen) -> ScreenTree:
    """Lines of text read from pixels, as text elements where each is."""
    roots = tuple(
        ElementNode(role="StaticText", label=line.text, frame=frame_of(line.box, screen), source=PIXELS)
        for line in lines
    )
    return ScreenTree(roots=roots, pixels=True)


class RecognitionCache:
    """The last few screenshots read, by their bytes and how they were read, most recently used kept longest."""

    def __init__(self, size: int = CACHE_SIZE) -> None:
        self._size = size
        self._kept: OrderedDict[tuple[bytes, tuple[object, ...]], tuple[RecognizedLine, ...]] = OrderedDict()

    @staticmethod
    def _key(jpeg: bytes, options: RecognitionOptions) -> tuple[bytes, tuple[object, ...]]:
        return hashlib.blake2b(jpeg, digest_size=16).digest(), options.reads_alike()

    def get(self, jpeg: bytes, options: RecognitionOptions) -> tuple[RecognizedLine, ...] | None:
        key = self._key(jpeg, options)
        lines = self._kept.get(key)
        if lines is not None:
            self._kept.move_to_end(key)
        return lines

    def put(self, jpeg: bytes, options: RecognitionOptions, lines: tuple[RecognizedLine, ...]) -> None:
        self._kept[self._key(jpeg, options)] = lines
        while len(self._kept) > self._size:
            self._kept.popitem(last=False)


#: Told the lines each reading kept, as they are drawn: the viewer's overlay.
OnRead = Callable[[tuple[RecognizedLine, ...]], None]


class OcrReader:
    """The text in a device's screenshot, as a tree."""

    def __init__(
        self,
        *,
        source: ScreenSource,
        screen: Screen,
        recognizer: TextRecognizer,
        options: RecognitionOptions,
        min_confidence: float,
        cache: RecognitionCache,
        on_read: OnRead | None = None,
    ) -> None:
        self._source = source
        self._screen = screen
        self._recognizer = recognizer
        self._options = options
        self._min_confidence = min_confidence
        self._cache = cache
        self._on_read = on_read

    async def read(self) -> ScreenTree:
        screen = self._screen
        width = min(screen.width_px, PIXELS_PER_POINT * screen.width_pt)
        shot = await self._source.screenshot(max_width=width, quality=OCR_QUALITY)
        lines = self._cache.get(shot.jpeg, self._options)
        if lines is None:
            lines = await self._recognizer.recognize(shot.jpeg, self._options)
            self._cache.put(shot.jpeg, self._options, lines)
        kept = tuple(line for line in lines if line.confidence >= self._min_confidence)
        if self._on_read is not None:
            self._on_read(kept)
        return tree_from_lines(kept, screen)


class OcrReaders:
    """Whether a device's screen may be read from its pixels, and the reader for it -- each device keeping its own
    cache until it ends."""

    def __init__(self, recognizer: TextRecognizer, *, supported: bool) -> None:
        self._recognizer = recognizer
        self._supported = supported
        self._caches: dict[str, RecognitionCache] = {}

    def enabled(self, config: SimConfig) -> bool:
        """Whether a scope reads pixels: its ``perception.ocr`` is on, on a Mac that can read them."""
        return self._supported and config.ocr_mode != "off"

    def reader(
        self, udid: str, source: ScreenSource, screen: Screen, config: SimConfig, on_read: OnRead | None = None
    ) -> OcrReader | None:
        """The reader of this device's pixels under its scope's settings as they are now; None when it reads none."""
        if not self.enabled(config):
            return None
        return OcrReader(
            source=source,
            screen=screen,
            recognizer=self._recognizer,
            options=RecognitionOptions.from_config(config),
            min_confidence=config.ocr_min_confidence / 100,
            cache=self._caches.setdefault(udid, RecognitionCache()),
            on_read=on_read,
        )

    def forget(self, udid: str) -> None:
        """The device ended: its readings are no use to the next one."""
        self._caches.pop(udid, None)

    async def close(self) -> None:
        self._caches.clear()
        await self._recognizer.close()
