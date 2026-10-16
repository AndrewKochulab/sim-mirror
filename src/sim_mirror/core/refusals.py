# SPDX-License-Identifier: Apache-2.0
"""Why a touch that would begin on an element is refused before it lands.

A step names an element by its ref and lands at its point. Some elements are listed but cannot be touched there: one
under a tab bar, a toolbar or the keyboard (`perception.occlusion`), where the touch would land on what covers it -- a
tap meant for a row would switch tabs, and the step would still say ok. `touch_refusal` says why such a touch is
refused, in words that tell the agent what to do instead; the step is not played.
"""

from __future__ import annotations

from sim_mirror.perception.occlusion import KEYBOARD
from sim_mirror.perception.snapshot import Element


def touch_refusal(ref: str, element: Element) -> str | None:
    """Why a touch beginning on this element is refused, or None when it may land."""
    if element.covered is None:
        return None
    named = f'{ref} "{element.label}"' if element.label else ref
    where = f"({round(element.x)},{round(element.y)})"
    if element.covered == KEYBOARD:
        instead = "close the keyboard or scroll it into view"
    else:
        instead = "scroll it into view with a swipe that starts on the content, not the bar,"
    return (
        f"{named} is under the {element.covered}: a touch at {where} would land on the {element.covered}. "
        f"First {instead} and then use {ref} again"
    )
