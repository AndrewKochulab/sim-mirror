# SPDX-License-Identifier: Apache-2.0
"""What WebDriverAgent does for a session on a real device: touches and buttons, typing, and the element tree.

**Touches** arrive as the stream of finger events every input sink takes (`connectors.base.InputSink`) and leave as
W3C pointer actions: a finger's stroke, from down to up, is sent whole with its timing, so a swipe keeps its speed --
and lands when the finger lifts, about half a second after a person started it. Points are the device's, in portrait,
as every connector's are; WebDriverAgent's are the interface's, so they are turned when the device is held sideways.
A touch, a key or a button is never queued behind a tree WebDriverAgent is still building for a read that gave up on
it: it waits for it `INPUT_WAIT_S` at most, and is refused unsent after that (`sendable`), rather than land late.

**Keys** are turned back into the text they type on a US keyboard, and typed through WebDriverAgent; Cmd+A then Delete
clears the focused field. **Text** of any kind -- accents, emoji, other scripts -- is typed whole (`WdaText`), so a
real device never needs the pasteboard. **Buttons**: Home and Lock; the others have no WebDriverAgent call.

**The element tree** is WebDriverAgent's (``/source``), made the document every reader answers: an element with a
label or an identifier, or that can be acted on, is kept, and so is a bar or the keyboard, which says what it covers
(`perception.occlusion`); an invisible one is not. An alert or a sheet is the modal in front. WebDriverAgent is asked
only for what is read here (`EXCLUDED`), and to read at most `real_devices.wda.source_max_children` children of each
element. A tree it does not build in time it goes on building, answering nothing else meanwhile, so the next read
does not ask it again until it is free (`WdaClient.idle`): the screen is read from its pixels until then -- and so is
a screenshot WebDriverAgent would take, from devicectl.
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
from sim_mirror.connectors.iphone.screen import Shots
from sim_mirror.connectors.iphone.wda_client import (
    MAX_CHILDREN_SETTING,
    SOURCE_MAX_CHILDREN,
    WdaClient,
    WdaError,
    WdaTimeout,
)
from sim_mirror.core.gestures import CHARACTER_KEYS, COMMAND_KEY, KEYS, SHIFT_KEY
from sim_mirror.perception.model import Frame
from sim_mirror.perception.occlusion import GROUPS, OCCLUDERS, foot_group
from sim_mirror.protocol import PanelButton

#: How long the device's orientation is trusted before it is asked again.
ORIENTATION_TTL_S = 2.0
#: How long the element tree is waited for, unless `real_devices.wda.source_timeout_ms` says otherwise. A screen
#: WebDriverAgent cannot read quickly -- a busy web page in Safari took over 30 s on an iPhone 14 Pro -- is read from
#: its pixels instead, and a wait for it to settle is not spent waiting on one request.
SOURCE_TIMEOUT_S = 5.0
#: How long the first element tree of a WebDriverAgent session is waited for, at least. The first is read cold -- the
#: device's accessibility server warming to the app on screen -- and took 5.4 s for Settings on an iPhone 17 Pro on
#: iOS 27.0 where every later one took under a second: given up on, it would turn a phone that reads fine into one said
#: not to, and leave WebDriverAgent busy building it for the next read.
COLD_SOURCE_TIMEOUT_S = 20.0
#: What WebDriverAgent leaves out of each element of a tree (its ``excluded_attributes``, as ``FBDebugCommands`` and
#: ``XCUIApplication+FBHelpers`` name them): all it would say that `kept` never reads -- each a lookup of its own for
#: every element. ``enabled`` and ``visible`` are read, and its ``rect`` stands for the ``frame`` string.
EXCLUDED = (
    "accessible",
    "nativeAccessibilityElement",
    "focused",
    "traits",
    "frame",
    "nativeFrame",
    "placeholderValue",
    "minValue",
    "maxValue",
)
SOURCE_PATH = f"/source?format=json&excluded_attributes={','.join(EXCLUDED)}"
#: How long a touch, text or a button waits for WebDriverAgent to finish an element tree an earlier read gave up on,
#: before it is refused unsent: sent sooner, it would wait behind the tree and land whenever that is done -- up to half
#: a minute later (`wda_client.REQUEST_TIMEOUT_S`), on a screen that may have moved since. As long as a snapshot waits
#: for a tree by default (``real_devices.wda.source_timeout_ms``): a tree still being built after that again is said
#: to the agent, which tries again, rather than waited out.
INPUT_WAIT_S = SOURCE_TIMEOUT_S
#: Said when a touch, text or a button is not sent because WebDriverAgent is still building that tree.
NOT_SENT = (
    "WebDriverAgent is still building an element tree an earlier read gave up on; the {what} was not sent -- try "
    "again in a moment"
)
#: The element types in front of everything else while they show.
MODALS = frozenset({"Alert", "Sheet"})
#: Said when a tree is not read because WebDriverAgent is still building one given up on before.
STILL_BUILDING = (
    "the element tree is still being built on the device for an earlier read, and WebDriverAgent answers nothing "
    "else until it is done"
)
#: The most points a finger's stroke is sent with. WebDriverAgent looks each point up against the app on screen -- an
#: accessibility round trip of about 240 ms -- so a stroke's points cost more than its length: measured on an iPhone 14
#: Pro, a 0.4 s swipe took 7 to 11 s with 25 points, and 1.3 to 1.6 s with 2.
STROKE_POINTS = 4
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
#: The buttons WebDriverAgent presses: Home, and Lock -- the side button is the same one.
WDA_BUTTONS: tuple[PanelButton, ...] = ("home", "lock", "side")
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


Stroke = list[tuple[float, float, float]]


def simplified(stroke: Stroke, most: int = STROKE_POINTS) -> Stroke:
    """A stroke with at most `most` points, each at the time it was reached: its first and last; the one before the
    last, so a flick leaves at the speed it did; and those farthest from the line from first to last, so a curve keeps
    its shape."""
    if len(stroke) <= most:
        return stroke
    (_, x0, y0), (_, x1, y1) = stroke[0], stroke[-1]

    def off_line(point: tuple[float, float, float]) -> float:
        _, x, y = point
        return abs((x1 - x0) * (y0 - y) - (x0 - x) * (y1 - y0))

    inner = range(1, len(stroke) - 2)
    kept = {0, len(stroke) - 2, len(stroke) - 1}
    kept |= set(sorted(inner, key=lambda i: off_line(stroke[i]), reverse=True)[: most - len(kept)])
    return [stroke[i] for i in sorted(kept)]


def pointer_actions(stroke: Stroke) -> list[dict[str, Any]]:
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


async def sendable(client: WdaClient, what: str, wait_s: float = INPUT_WAIT_S) -> None:
    """Refuse `what` -- a touch, text, a button press -- rather than send it while WebDriverAgent is still serving a
    request given up on, once it has waited `wait_s` for it to finish: sent then, it would land late, unasked."""
    if not await client.free_within(wait_s):
        raise ConnectorError(NOT_SENT.format(what=what))


class WdaInput:
    """Touches, buttons and keys, through WebDriverAgent; each waits `wait_s` at most for it to be free (`sendable`)."""

    def __init__(
        self,
        client: WdaClient,
        orientation: Orientation,
        clock: Callable[[], float] = time.monotonic,
        *,
        wait_s: float = INPUT_WAIT_S,
    ) -> None:
        self._client = client
        self._orientation = orientation
        self._clock = clock
        self._wait_s = wait_s

    async def hid(self, events: AsyncIterable[HidEvent]) -> None:
        stroke: Stroke = []
        typing = Typing(self._client, self._wait_s)
        async for event in events:
            if event.kind == "touch":
                await typing.flush()
                # Before the orientation is asked too: that is a request of its own.
                await sendable(self._client, "touch", self._wait_s)
                turn = await self._orientation.turn()
                x, y = turn.to_interface(event.x, event.y)
                stroke.append((self._clock(), round(x, 1), round(y, 1)))
                if event.phase == "up":
                    await self._client.in_session("POST", "/actions", {"actions": pointer_actions(simplified(stroke))})
                    stroke = []
            elif event.kind == "button":
                await typing.flush()
                if event.phase == "down":
                    await sendable(self._client, "button press", self._wait_s)
                    await self._press(event.button)
            else:
                await typing.key(event)
        if stroke:
            await sendable(self._client, "touch", self._wait_s)
            await self._client.in_session("POST", "/actions", {"actions": pointer_actions(simplified(stroke))})
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

    def __init__(self, client: WdaClient, wait_s: float = INPUT_WAIT_S) -> None:
        self._client = client
        self._wait_s = wait_s
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
            await clear_focused(self._client, self._wait_s)
            return
        self._selected = False
        typed = _SPECIAL.get(event.code) or _TYPED.get((event.code, SHIFT_KEY in self._held))
        if typed is not None:
            self._text += typed

    async def flush(self) -> None:
        if self._text:
            text, self._text = self._text, ""
            await type_text(self._client, text, self._wait_s)


async def type_text(client: WdaClient, text: str, wait_s: float = INPUT_WAIT_S) -> None:
    await sendable(client, "text", wait_s)
    await client.in_session("POST", "/wda/keys", {"value": list(text)})


async def clear_focused(client: WdaClient, wait_s: float = INPUT_WAIT_S) -> None:
    """Empty the field that has the keyboard's focus."""
    await sendable(client, "text", wait_s)
    active = await client.in_session("GET", "/element/active")
    element = active.get(ELEMENT_KEY) or active.get("ELEMENT") if isinstance(active, dict) else None
    if not element:
        raise ConnectorError("no field has the keyboard's focus to clear")
    await client.in_session("POST", f"/element/{element}/clear")


class WdaText:
    """Text typed whole, whatever it holds."""

    def __init__(self, client: WdaClient, *, wait_s: float = INPUT_WAIT_S) -> None:
        self._client = client
        self._wait_s = wait_s

    async def type(self, text: str) -> None:
        await type_text(self._client, text, self._wait_s)


class WdaShots:
    """WebDriverAgent's screenshots, taken the way devicectl's are read (`screen.DevicectlScreen`): a PNG written
    where asked. They come about three times a second, over the cable.

    While WebDriverAgent is still serving a request given up on, a screenshot would wait behind it: `fallback` --
    devicectl -- takes it instead, when there is one."""

    def __init__(self, client: WdaClient, fallback: Shots | None = None) -> None:
        self._client = client
        self._fallback = fallback

    async def screenshot(self, udid: str, destination: Path) -> None:
        if self._fallback is not None and not await self._client.idle():
            await self._fallback.screenshot(udid, destination)
            return
        encoded = await self._client.call("GET", "/screenshot")
        try:
            destination.write_bytes(base64.b64decode(str(encoded), validate=True))
        except binascii.Error as exc:
            raise ConnectorError("WebDriverAgent's screenshot is not a picture") from exc


class WdaReader:
    """The element tree, as WebDriverAgent reads it: waited for `timeout_s` -- the first of the session, read cold, for
    `cold_timeout_s` at least -- each element read with at most `max_children` of its children."""

    def __init__(
        self,
        client: WdaClient,
        orientation: Orientation,
        screen: Screen,
        *,
        timeout_s: float = SOURCE_TIMEOUT_S,
        max_children: int = SOURCE_MAX_CHILDREN,
        cold_timeout_s: float = COLD_SOURCE_TIMEOUT_S,
    ) -> None:
        self._client = client
        self._orientation = orientation
        self._screen = screen
        self._timeout_s = timeout_s
        self._max_children = max_children
        self._cold_timeout_s = cold_timeout_s
        self._warm = False

    async def accessibility(self) -> dict[str, Any]:
        if not await self._client.idle():
            raise WdaError(STILL_BUILDING)
        await self._client.configure({MAX_CHILDREN_SETTING: self._max_children})
        turn = await self._orientation.turn()
        timeout_s = self._timeout_s if self._warm else max(self._timeout_s, self._cold_timeout_s)
        try:
            found = await self._client.in_session(
                "GET", SOURCE_PATH, timeout_s=timeout_s, shape=lambda source: self._found(source, turn)
            )
        except WdaTimeout as exc:
            raise WdaError(
                f"WebDriverAgent did not build the element tree within {timeout_s:g} s "
                "(real_devices.wda.source_timeout_ms), and goes on building it on the device"
            ) from exc
        self._warm = True
        elements, modal, notes = found
        return {
            "backend": "wda",
            "elements": elements,
            "modal": modal,
            "screen": {"coordinate_space": "screen", "width": self._screen.width_pt, "height": self._screen.height_pt},
            "truncated": False,
            "notes": notes,
        }

    def _found(self, source: Any, turn: Turn) -> tuple[list[dict[str, Any]], dict[str, Any] | None, list[str]]:
        """A tree's elements, the modal in front, and what the tree leaves out."""
        if not isinstance(source, dict):
            return [], None, []
        notes = []
        if cut_short(source, self._max_children):
            notes.append(
                f"WebDriverAgent read only the first {self._max_children} items of a list on this screen "
                "(real_devices.wda.source_max_children): what is further down it is not listed; sim_screenshot shows it"
            )
        return kept(source, turn), modal_of(source), notes


def _text(value: Any) -> str | None:
    return None if value is None or value == "" else str(value)


def _flag(value: Any) -> bool:
    return value in (True, "1", 1, "true")


def _children(node: dict[str, Any]) -> list[dict[str, Any]]:
    return [child for child in node.get("children") or () if isinstance(child, dict)]


def modal_of(node: dict[str, Any]) -> dict[str, Any] | None:
    """The alert or sheet in front -- the last one in the tree, which is drawn over those before it -- as the document
    names a modal; None when none shows."""
    found: dict[str, Any] | None = None
    for child in _children(node):
        found = modal_of(child) or found
    kind = _text(node.get("type"))
    if found is None and kind in MODALS and ("isVisible" not in node or _flag(node.get("isVisible"))):
        found = {"type": kind, "label": _text(node.get("label"))}
    return found


def cut_short(node: dict[str, Any], most: int) -> bool:
    """Whether WebDriverAgent may have left children out of an element, reading at most `most` of each one's."""
    children = _children(node)
    return len(children) >= most or any(cut_short(child, most) for child in children)


def _at_foot(kind: str | None, rect: Any, turn: Turn) -> bool:
    """Whether a bare group lies across the foot of the screen held upright, as the toolbar of a docked search field
    does (`occlusion.foot_group`)."""
    if kind not in GROUPS or turn.sideways or not isinstance(rect, dict):
        return False
    framed = turn.frame(rect)
    frame = Frame(framed["x"], framed["y"], framed["width"], framed["height"])
    return foot_group(frame, turn.width, turn.height)


def kept(node: dict[str, Any], turn: Turn) -> list[dict[str, Any]]:
    """The elements of a node worth an agent's reading, each with those under it.

    A node WebDriverAgent calls invisible is left out, but not what is under it: on iOS 26 it calls a list invisible
    whose cells it calls visible (Settings' own list), and each of those says for itself whether it shows. A bare group
    across the foot of the screen is kept, though nothing names it: on iOS 27 it is the toolbar a docked search field
    is on, and says how far above the field it takes a tap (`perception.occlusion`); no line is made of it.
    """
    children = [child for each in node.get("children") or () if isinstance(each, dict) for child in kept(each, turn)]
    if "isVisible" in node and not _flag(node.get("isVisible")):
        return children
    identifier = _text(node.get("rawIdentifier")) or _text(node.get("name"))
    label = _text(node.get("label"))
    kind = _text(node.get("type"))
    rect = node.get("rect")
    named = label or identifier or kind in ACTIONABLE or kind in OCCLUDERS
    if not (named or _at_foot(kind, rect, turn)) or kind == "Application":
        return children
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
