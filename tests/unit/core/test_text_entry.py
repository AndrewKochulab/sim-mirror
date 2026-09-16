# SPDX-License-Identifier: Apache-2.0
"""How text reaches a device: typed as keys where it has them and the Mac's layout is US-shaped, else pasted."""

from __future__ import annotations

import pytest

from sim_mirror.core import gestures
from sim_mirror.core.text_entry import paste_refused, text_entry
from sim_mirror.testing.fakes import FakeKeyboard


async def test_auto_types_what_has_keys_while_the_macs_layout_is_us_shaped_and_pastes_otherwise() -> None:
    us, other = FakeKeyboard(us=True), FakeKeyboard(us=False)
    typed = await text_entry("Hi!", "auto", us)
    assert typed.events == gestures.typed("Hi!") and typed.pasted == "" and us.asked == 1
    pasted = await text_entry("Hi!", "auto", other)
    assert pasted.events == gestures.paste() and pasted.pasted == "Hi!" and other.asked == 1
    assert (typed.why_pasted, pasted.why_pasted) == ("", "the Mac's keyboard layout is not US or ABC")


async def test_keys_types_without_asking_and_paste_pastes_without_asking() -> None:
    keyboard = FakeKeyboard(us=False)
    assert (await text_entry("wifi", "keys", keyboard)).events == gestures.typed("wifi")
    pasted = await text_entry("wifi", "paste", keyboard)
    assert (pasted.pasted, pasted.why_pasted) == ("wifi", "device.typing says to paste")
    assert keyboard.asked == 0


@pytest.mark.parametrize("typing", ["auto", "keys", "paste"])
async def test_text_with_a_character_no_key_types_is_pasted_whole_whatever_the_setting(typing: str) -> None:
    keyboard = FakeKeyboard(us=True)
    entry = await text_entry("café 😀", typing, keyboard)
    assert (entry.events, entry.pasted, keyboard.asked) == (gestures.paste(), "café 😀", 0)
    assert entry.why_pasted == "'é' has no key to type it with"


@pytest.mark.parametrize(
    ("runtime", "refused"),
    [
        ("iOS 27.0", True),
        ("iOS 28.1", True),
        ("iOS 26.5", False),
        ("iOS 18.6", False),
        ("watchOS 27.0", False),
        ("", False),
    ],
)
def test_a_paste_is_refused_without_asking_from_ios_27(runtime: str, refused: bool) -> None:
    assert paste_refused(runtime) is refused
