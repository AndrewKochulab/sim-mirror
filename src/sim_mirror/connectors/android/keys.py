# SPDX-License-Identifier: Apache-2.0
"""Keys and buttons as Android's keycodes.

Input reaches every connector as USB HID usage ids (`core.gestures`), a US keyboard's. Android names a key by its own
code (``KeyEvent.KEYCODE_*``); Command, which SimMirror holds for select all and paste, is Android's Control. A device's
buttons are its own keys too: Home, Back, the app switcher, power for Lock, and the volume.
"""

from __future__ import annotations

from collections.abc import Mapping

from sim_mirror.core.gestures import COMMAND_KEY, KEYS, SHIFT_KEY
from sim_mirror.protocol import PanelButton

#: The buttons an Android device has, and the key each is.
BUTTON_KEYCODES: Mapping[str, int] = {
    "home": 3,
    "back": 4,
    "app_switch": 187,
    "lock": 26,
    "volume_up": 24,
    "volume_down": 25,
}
#: What an Android session presses, in the order a viewer shows them.
ANDROID_BUTTONS: tuple[PanelButton, ...] = ("back", "home", "app_switch", "lock", "volume_up", "volume_down")

CTRL_LEFT = 113
SHIFT_LEFT = 59
META_SHIFT_ON = 0x1
META_CTRL_ON = 0x1000
#: The modifier keys, as Android's keys and meta-state flags.
MODIFIERS: Mapping[int, tuple[int, int]] = {
    COMMAND_KEY: (CTRL_LEFT, META_CTRL_ON),
    SHIFT_KEY: (SHIFT_LEFT, META_SHIFT_ON),
}

_NAMED = {
    "return": 66, "escape": 111, "delete": 67, "tab": 61, "space": 62, "right": 22, "left": 21, "down": 20, "up": 19,
}  # fmt: skip
#: HID usage ids of punctuation, US layout, as Android's keys.
_PUNCTUATION = {45: 69, 46: 70, 47: 71, 48: 72, 49: 73, 51: 74, 52: 75, 53: 68, 54: 55, 55: 56, 56: 76}
#: Every HID usage id SimMirror sends that has an Android key: letters, digits, the named keys and punctuation.
HID_KEYCODES: Mapping[int, int] = {
    **{4 + letter: 29 + letter for letter in range(26)},
    **{30 + digit: 8 + digit for digit in range(9)},
    39: 7,
    **{KEYS[name]: code for name, code in _NAMED.items()},
    **_PUNCTUATION,
}


def keycode_of(usage: int) -> int | None:
    """The Android key a HID usage id is, or None for one Android has no key for."""
    return HID_KEYCODES.get(usage)
