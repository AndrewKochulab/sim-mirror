# SPDX-License-Identifier: Apache-2.0
"""The text viewers outline over a device's screen: boxes within the screen, each reading in place of the last, and a
clear told only when there is something to clear."""

from __future__ import annotations

from sim_mirror.core.events import EventBus
from sim_mirror.core.text_overlay import HOLD_MS, TextOverlay, text_boxes
from sim_mirror.perception.ocr import RecognizedLine, TextBox
from sim_mirror.protocol import SCREEN_TEXT_MAX_BOXES


def line(
    text: str, x: float = 0.1, y: float = 0.2, w: float = 0.3, h: float = 0.04, confidence: float = 0.9
) -> RecognizedLine:
    return RecognizedLine(text, confidence, TextBox(x, y, w, h))


def test_lines_are_boxes_within_the_screen_blank_ones_left_out_and_no_more_than_a_viewer_takes() -> None:
    assert text_boxes([line("Sign in", 0.123456, 0.5, 0.25, 0.02, confidence=0.98765), line("  ")]) == [
        {"text": "Sign in", "confidence": 0.988, "x": 0.1235, "y": 0.5, "w": 0.25, "h": 0.02}
    ]
    assert text_boxes([line("Edge", -0.2, 1.4, 2.0, -1.0)]) == [
        {"text": "Edge", "confidence": 0.9, "x": 0.0, "y": 1.0, "w": 1.0, "h": 0.0}
    ]
    assert len(text_boxes([line(f"row {n}") for n in range(SCREEN_TEXT_MAX_BOXES + 5)])) == SCREEN_TEXT_MAX_BOXES
    assert text_boxes([]) == []


def test_each_reading_replaces_the_last_and_a_clear_is_told_only_when_boxes_are_drawn() -> None:
    bus = EventBus()
    heard = bus.subscribe()
    overlay = TextOverlay(bus)
    overlay.hide()
    overlay.show([])
    assert heard.empty() and not overlay.shown
    overlay.show([line("Sign in")])
    overlay.show([line("Welcome")], hold_ms=5)
    assert overlay.shown
    overlay.hide()
    overlay.hide()
    overlay.show([line("Again")])
    overlay.show([line(" ")])
    told = [heard.get_nowait() for _ in range(heard.qsize())]
    assert [(event["id"], event["hold_ms"], [box["text"] for box in event["boxes"]]) for event in told] == [
        ("t1", HOLD_MS, ["Sign in"]),
        ("t2", 5, ["Welcome"]),
        ("t3", 0, []),
        ("t4", HOLD_MS, ["Again"]),
        ("t5", 0, []),
    ]
    assert {event["type"] for event in told} == {"screen_text"} and not overlay.shown
