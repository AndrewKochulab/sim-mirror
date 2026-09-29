# SPDX-License-Identifier: Apache-2.0
"""What WebDriverAgent does for a session on a real device: touches and buttons, typing, and the element tree.

**Touches** arrive as the stream of finger events every input sink takes (`connectors.base.InputSink`) and leave as
W3C pointer actions: a finger's stroke, from down to up, is sent whole with its timing, so a swipe keeps its speed --
and lands when the finger lifts, about half a second after a person started it. Points are the device's, in portrait,
as every connector's are; WebDriverAgent's are the interface's, so they are turned when the device is held sideways.

**Keys** are turned back into the text they type on a US keyboard, and typed through WebDriverAgent; Cmd+A then Delete
clears the focused field. **Text** of any kind -- accents, emoji, other scripts -- is typed whole (`WdaText`), so a
real device never needs the pasteboard. **Buttons**: Home and Lock; the others have no WebDriverAgent call.

**The element tree** is WebDriverAgent's (``/source``), made the document every reader answers: an element with a
label or an identifier, or that can be acted on, is kept; an invisible one is not.
"""

from __future__ import annotations

import base64
import binascii
import time
from collections.abc import AsyncIterable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sim_mirror.connectors.base import ConnectorError, HidEvent, Screen
from sim_mirror.connectors.iphone.wda_client import WdaClient
from sim_mirror.core.gestures import CHARACTER_KEYS, COMMAND_KEY, KEYS, SHIFT_KEY

#: How long the device's orientation is trusted before it is asked again.
ORIENTATION_TTL_S = 2.0
#: A key's code and whether Shift was held, back to the character it types.
_TYPED = {(code, shift): character for character, (code, shift) in CHARACTER_KEYS.items()}
#: Keys that type no character of their own, as the characters XCUITest types them with.
_SPECIAL = {
    KEYS["delete"]: "\b",
    KEYS["tab"]: "\t",
    KEYS["escape"]: "\x1b",
    KEYS["up"]: "",
    KEYS["down"]: "",
    KEYS["left"]: "",
    KEYS["right"]: "",
}
_A_KEY = CHARACTER_KEYS["a"][0]
#: The element types kept whatever they say: the native helper's actionable roles.
ACTIONABLE = frozenset({
    "Button", "Cell", "TextField", "SecureTextField", "SearchField", "Switch", "Toggle", "Link", "MenuItem", "Slider",
    "CheckBox", "RadioButton", "SegmentedControl", "Stepper", "PopUpButton", "Picker", "PickerWheel", "Tab", "Key",
    "DisclosureTriangle",
})  # fmt: skip
#: The field an element id comes back in.
ELEMENT_KEY = "element-6066-11e4-a52e-4f735466cecf"


@dataclass(frozen=True)
class Turn:
    """How a portrait point lies in the interface's points as the device is held.

    WebDriverAgent says ``PORTRAIT`` or ``LANDSCAPE``, and nothing finer. A sideways screen is the portrait one
    turned a quarter the way the cable's picture is turned back upright (the helper's `CaptureTurn`), so a point
    touched on the picture lands where it was shown.
    """

    orientation: str
    width: float
    height: float

    @property
    def sideways(self) -> bool:
        return self.orientation == "LANDSCAPE"

    def to_interface(self, x: float, y: float) -> tuple[float, float]:
        return (y, self.width - x) if self.sideways else (x, y)

    def to_portrait(self, x: float, y: float) -> tuple[float, float]:
        return (self.width - y, x) if self.sideways else (x, y)

    def frame(self, rect: dict[str, Any]) -> dict[str, float]:
        """An interface rectangle as the portrait one it covers."""
        x, y = float(rect.get("x") or 0), float(rect.get("y") or 0)
        width, height = float(rect.get("width") or 0), float(rect.get("height") or 0)
        corners = [self.to_portrait(x, y), self.to_portrait(x + width, y + height)]
        left, right = sorted(corner[0] for corner in corners)
        top, bottom = sorted(corner[1] for corner in corners)
        return {"x": left, "y": top, "width": right - left, "height": bottom - top}


class Orientation:
    """The device's orientation, asked of WebDriverAgent at most every `ORIENTATION_TTL_S`."""

    def __init__(self, client: WdaClient, screen: Screen, clock: Callable[[], float] = time.monotonic) -> None:
        self._client = client
        self._screen = screen
        self._clock = clock
        self._known: tuple[float, Turn] | None = None

    async def turn(self) -> Turn:
        now = self._clock()
        if self._known is None or now - self._known[0] >= ORIENTATION_TTL_S:
            said = await self._client.in_session("GET", "/orientation")
            self._known = (now, Turn(str(said or "PORTRAIT"), self._screen.width_pt, self._screen.height_pt))
        return self._known[1]


def pointer_actions(stroke: list[tuple[float, float, float]]) -> list[dict[str, Any]]:
    """A finger's stroke -- its points and when it reached each, in seconds -- as W3C pointer actions."""
    (start, x, y), *moves = stroke
    actions: list[dict[str, Any]] = [
        {"type": "pointerMove", "duration": 0, "x": round(x, 1), "y": round(y, 1)},
        {"type": "pointerDown", "button": 0},
    ]
    at = start
    for when, x, y in moves:
        actions.append({"type": "pointerMove", "duration": max(0, round((when - at) * 1000)), "x": x, "y": y})
        at = when
    actions.append({"type": "pointerUp", "button": 0})
    return [{"type": "pointer", "id": "finger", "parameters": {"pointerType": "touch"}, "actions": actions}]


class WdaInput:
    """Touches, buttons and keys, through WebDriverAgent."""

    def __init__(
        self, client: WdaClient, orientation: Orientation, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._client = client
        self._orientation = orientation
        self._clock = clock

    async def hid(self, events: AsyncIterable[HidEvent]) -> None:
        stroke: list[tuple[float, float, float]] = []
        typing = Typing(self._client)
        async for event in events:
            if event.kind == "touch":
                await typing.flush()
                turn = await self._orientation.turn()
                x, y = turn.to_interface(event.x, event.y)
                stroke.append((self._clock(), round(x, 1), round(y, 1)))
                if event.phase == "up":
                    await self._client.in_session("POST", "/actions", {"actions": pointer_actions(stroke)})
                    stroke = []
            elif event.kind == "button":
                await typing.flush()
                if event.phase == "down":
                    await self._press(event.button)
            else:
                await typing.key(event)
        if stroke:
            await self._client.in_session("POST", "/actions", {"actions": pointer_actions(stroke)})
        await typing.flush()

    async def _press(self, button: str) -> None:
        if button == "home":
            await self._client.call("POST", "/wda/homescreen")
        elif button in ("lock", "side"):
            await self._client.in_session("POST", "/wda/lock")
        else:
            raise ConnectorError(f"the {button} button cannot be pressed through WebDriverAgent")


class Typing:
    """Key presses turned back into text, sent together; Cmd+A then Delete clears the focused field."""

    def __init__(self, client: WdaClient) -> None:
        self._client = client
        self._held: set[int] = set()
        self._text = ""
        self._selected = False

    async def key(self, event: HidEvent) -> None:
        if event.phase == "up":
            self._held.discard(event.code)
            return
        if event.code in (SHIFT_KEY, COMMAND_KEY):
            self._held.add(event.code)
            return
        if COMMAND_KEY in self._held:
            self._selected = event.code == _A_KEY
            return
        if self._selected and event.code == KEYS["delete"]:
            self._selected = False
            await self.flush()
            await clear_focused(self._client)
            return
        self._selected = False
        typed = _SPECIAL.get(event.code) or _TYPED.get((event.code, SHIFT_KEY in self._held))
        if typed is not None:
            self._text += typed

    async def flush(self) -> None:
        if self._text:
            text, self._text = self._text, ""
            await type_text(self._client, text)


async def type_text(client: WdaClient, text: str) -> None:
    await client.in_session("POST", "/wda/keys", {"value": list(text)})


async def clear_focused(client: WdaClient) -> None:
    """Empty the field that has the keyboard's focus."""
    active = await client.in_session("GET", "/element/active")
    element = active.get(ELEMENT_KEY) or active.get("ELEMENT") if isinstance(active, dict) else None
    if not element:
        raise ConnectorError("no field has the keyboard's focus to clear")
    await client.in_session("POST", f"/element/{element}/clear")


class WdaText:
    """Text typed whole, whatever it holds."""

    def __init__(self, client: WdaClient) -> None:
        self._client = client

    async def type(self, text: str) -> None:
        await type_text(self._client, text)


class WdaShots:
    """WebDriverAgent's screenshots, taken the way devicectl's are read (`screen.DevicectlScreen`): a PNG written
    where asked. They come about three times a second, over the cable."""

    def __init__(self, client: WdaClient) -> None:
        self._client = client

    async def screenshot(self, udid: str, destination: Path) -> None:
        encoded = await self._client.call("GET", "/screenshot")
        try:
            destination.write_bytes(base64.b64decode(str(encoded), validate=True))
        except binascii.Error as exc:
            raise ConnectorError("WebDriverAgent's screenshot is not a picture") from exc


class WdaReader:
    """The element tree, as WebDriverAgent reads it."""

    def __init__(self, client: WdaClient, orientation: Orientation, screen: Screen) -> None:
        self._client = client
        self._orientation = orientation
        self._screen = screen

    async def accessibility(self) -> dict[str, Any]:
        source = await self._client.call("GET", "/source?format=json")
        turn = await self._orientation.turn()
        return {
            "backend": "wda",
            "elements": kept(source, turn) if isinstance(source, dict) else [],
            "modal": None,
            "screen": {"coordinate_space": "screen", "width": self._screen.width_pt, "height": self._screen.height_pt},
            "truncated": False,
        }


def _text(value: Any) -> str | None:
    return None if value is None or value == "" else str(value)


def _flag(value: Any) -> bool:
    return value in (True, "1", 1, "true")


def kept(node: dict[str, Any], turn: Turn) -> list[dict[str, Any]]:
    """The elements of a node worth an agent's reading, each with those under it.

    A node WebDriverAgent calls invisible is left out, but not what is under it: on iOS 26 it calls a list invisible
    whose cells it calls visible (Settings' own list), and each of those says for itself whether it shows.
    """
    children = [child for each in node.get("children") or () if isinstance(each, dict) for child in kept(each, turn)]
    if "isVisible" in node and not _flag(node.get("isVisible")):
        return children
    identifier = _text(node.get("rawIdentifier")) or _text(node.get("name"))
    label = _text(node.get("label"))
    kind = _text(node.get("type"))
    if not (label or identifier or kind in ACTIONABLE) or kind == "Application":
        return children
    rect = node.get("rect")
    return [
        {
            "type": kind,
            "subrole": None,
            "label": label,
            "title": None,
            "identifier": identifier,
            "value": _text(node.get("value")),
            "frame": turn.frame(rect if isinstance(rect, dict) else {}),
            "traits": None,
            "enabled": _flag(node.get("isEnabled", True)),
            "children": children,
        }
    ]
