# SPDX-License-Identifier: Apache-2.0
"""Whether the Mac's keyboard layout puts each character where a US keyboard does.

A key SimMirror presses on a simulator goes through the simulator's keyboard layout, which follows the Mac's current
input source: with a Ukrainian layout chosen, the keys for "wifi" type "цшаш". So text is typed as keys only while the
Mac's layout is one of `US_LAYOUTS` -- read at the moment of typing, since a person switches layouts as they go -- and
pasted otherwise (`core/text_entry.py`).

The layout is read with ``defaults``, which asks the preferences daemon rather than a file it may not have written
yet. A layout that cannot be read counts as not US.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sim_mirror.platform import process

#: The Mac keyboard layouts whose keys are where a US keyboard's are, by input source id.
US_LAYOUTS = frozenset({"com.apple.keylayout.ABC", "com.apple.keylayout.US"})
LAYOUT_QUERY = ("defaults", "read", "com.apple.HIToolbox", "AppleCurrentKeyboardLayoutInputSourceID")

#: Whether the keys SimMirror presses type the characters they are for; `mac_keyboard_is_us`, or a fake.
KeyboardCheck = Callable[[], Awaitable[bool]]


async def mac_keyboard_is_us(run: process.Runner = process.run) -> bool:
    code, out = await run(LAYOUT_QUERY)
    return code == 0 and out.strip() in US_LAYOUTS
