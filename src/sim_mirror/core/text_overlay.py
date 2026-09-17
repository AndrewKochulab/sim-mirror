# SPDX-License-Identifier: Apache-2.0
"""The text a device's viewers outline over its screen: what the last reading of its pixels found.

While a scope's ``perception.ocr_overlay`` is on, each reading of a device's pixels is told to its viewers as a
`ScreenText` (`protocol/v1/screen-text.schema.json`): the lines it kept, as boxes in shares of the screen, replacing
whatever they drew before. Boxes that no longer hold are cleared -- when a snapshot reads the screen without its pixels
or with the overlay off, before an agent's gesture that changes the screen, and when a person touches it -- and a
viewer lets go of boxes nobody cleared after `HOLD_MS`, for a screen that changes by itself.

The boxes are the viewer's own, drawn over the screen: a screenshot or a recording of the device never contains them.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterable

from sim_mirror.core.events import EventBus
from sim_mirror.perception.ocr import RecognizedLine
from sim_mirror.protocol import SCREEN_TEXT_MAX_BOXES, TextBox, screen_text, text_box

#: The longest a viewer keeps boxes nobody cleared.
HOLD_MS = 60_000
#: Gestures that leave the screen as it is: announcing one keeps the boxes.
STILL = frozenset({"look", "pause"})


def _share(value: float) -> float:
    return round(min(1.0, max(0.0, value)), 4)


def text_boxes(lines: Iterable[RecognizedLine]) -> list[TextBox]:
    """Lines read from pixels as the boxes a viewer draws: within the screen, blank ones left out, at most
    `SCREEN_TEXT_MAX_BOXES`."""
    boxes = [
        text_box(line.text, round(line.confidence, 3), _share(line.box.x), _share(line.box.y),
                 _share(line.box.width), _share(line.box.height))
        for line in lines
        if line.text.strip()
    ]  # fmt: skip
    return boxes[:SCREEN_TEXT_MAX_BOXES]


class TextOverlay:
    """One device's text boxes, told to its viewers on its event bus."""

    def __init__(self, events: EventBus) -> None:
        self._events = events
        self._ids = itertools.count(1)
        self._shown = False

    @property
    def shown(self) -> bool:
        """Whether viewers were last told boxes to draw."""
        return self._shown

    def show(self, lines: Iterable[RecognizedLine], *, hold_ms: int = HOLD_MS) -> None:
        """Tell viewers the boxes a reading found, in place of any before; a reading that found none clears them."""
        boxes = text_boxes(lines)
        if not boxes:
            self.hide()
            return
        self._events.publish(screen_text(f"t{next(self._ids)}", boxes, hold_ms=hold_ms))
        self._shown = True

    def hide(self) -> None:
        """Tell viewers to clear the boxes -- only when there are any, so a touch moving costs nothing."""
        if not self._shown:
            return
        self._events.publish(screen_text(f"t{next(self._ids)}"))
        self._shown = False
