# SPDX-License-Identifier: Apache-2.0
"""How text reaches a device: typed as key presses, or put on its pasteboard and pasted.

Both a viewer's text and an agent's ``type`` step come here, so they arrive the same way. Measured on iOS 26.5 and
27.0 with a probe app (#27):

* **Pasting** keeps every character exactly, but iOS 26 asks "Allow Paste" first, and iOS 27 refuses the paste
  silently -- the pasteboard holds the text and Cmd+V arrives, yet nothing appears.
* **Typing keys** works on both without a prompt, and needs no permission. It types only what a US keyboard has keys
  for, through the Mac's keyboard layout (`platform/keyboard.py`), and iOS applies the keyboard's own habits as it
  would for a person: smart punctuation turns ``'`` into ``’`` and ``--`` into ``—`` in a field that allows it.
  Neither iOS autocorrected or capitalized what was typed on the hardware keyboard.

So ``device.typing`` decides, per scope:

* ``auto`` (the default) types the text when every character has a key and the Mac's layout is US-shaped, and pastes
  it otherwise;
* ``keys`` always types what has keys -- for a layout SimMirror does not know is US-shaped -- and pastes the rest;
* ``paste`` always pastes, for text that must arrive character for character, on iOS 26 and earlier.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sim_mirror.core import gestures
from sim_mirror.platform.keyboard import KeyboardCheck

#: The first iOS that refuses a paste SimMirror sends, without asking the person.
PASTE_REFUSED_FROM = 27

_IOS = re.compile(r"\AiOS (?P<major>\d+)")


@dataclass(frozen=True)
class TextEntry:
    """The events that enter the text, the text to put on the pasteboard first -- "" when it is typed -- and why it
    is pasted, said so an agent can tell what to change."""

    events: list[gestures.Timed]
    pasted: str = ""
    why_pasted: str = ""


async def text_entry(text: str, typing: str, keyboard_is_us: KeyboardCheck) -> TextEntry:
    """How `text` goes in, under the scope's ``device.typing``. The Mac's layout is only asked about when it decides."""
    keys = gestures.typed(text)
    if keys is None:
        keyless = next(character for character in text if character not in gestures.CHARACTER_KEYS)
        why = f"{keyless!r} has no key to type it with"
    elif typing == "paste":
        why = "device.typing says to paste"
    elif typing == "keys" or await keyboard_is_us():
        return TextEntry(keys)
    else:
        why = "the Mac's keyboard layout is not US or ABC"
    return TextEntry(gestures.paste(), text, why)


def paste_refused(runtime: str) -> bool:
    """Whether a device on this runtime -- ``iOS 27.0`` -- refuses a paste it did not ask for."""
    match = _IOS.match(runtime)
    return match is not None and int(match.group("major")) >= PASTE_REFUSED_FROM
