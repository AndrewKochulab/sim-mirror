# SPDX-License-Identifier: Apache-2.0
"""What is on an Android device's screen, as uiautomator dumps it, made the document every reader answers.

``uiautomator dump /dev/tty`` writes the app's window as XML: each view's class, text, content description, resource
id, state and bounds, in the turned display's pixels, under a root saying how many quarters it is turned. It takes
two to four seconds (measured on an API 36 emulator), and fails on a screen that never stops moving -- then the
screen's pixels are read instead (`perception.ocr`).

**Roles** are the vocabulary every reader speaks (`perception.snapshot`): a field is a ``TextField`` (a password's a
``SecureTextField``), a switch a ``Switch``, a check box or radio button a ``Toggle``, a seek bar a ``Slider``, text a
``StaticText``, a picture an ``Image``, a list a ``ScrollView``, a toolbar a ``NavigationBar``, bottom navigation a
``TabBar``, and anything a person can tap a ``Button``. A tappable container with no words of its own -- a Settings row
is a layout holding its title and summary -- takes the words of what it holds, as TalkBack reads it, and those words
are not listed again under it; what can be tapped inside it still is.

**What covers the app** is not in its window: the keyboard, the status bar and the system navigation bar are windows of
their own. ``dumpsys window windows`` (40 ms) says where each one is drawn now -- the keyboard where it takes touches --
and each is added as a ``Keyboard``, a ``StatusBar`` and a ``Toolbar``, so a tap under one is refused as covered
(`perception.occlusion`).

Bounds become the interface's points, and the document says which way the interface is turned
(`perception.orientation`).
"""

from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ElementTree
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sim_mirror.connectors.android.geometry import orientation_of
from sim_mirror.connectors.base import ConnectorError, Screen
from sim_mirror.platform.adb import Adb, AdbError

#: How long a UI dump may take.
DUMP_TIMEOUT_S = 10.0
END = "</hierarchy>"
_BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
_WINDOW = re.compile(r"^  Window #\d+ Window\{\S+ \S+ ([^}]*)\}:$", re.M)
_TYPE = re.compile(r"\bty=(\w+)")
_FRAME = re.compile(r"\bframe=\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
_TOUCHABLE = re.compile(r"touchable region=SkRegion\(\((-?\d+),(-?\d+),(-?\d+),(-?\d+)\)\)")

#: Classes by the last part of their name, as the roles readers speak.
ROLES: Mapping[str, str] = {
    "EditText": "TextField", "AutoCompleteTextView": "TextField", "TextInputEditText": "TextField",
    "SearchView": "SearchField",
    "Switch": "Switch", "SwitchCompat": "Switch", "SwitchMaterial": "Switch", "MaterialSwitch": "Switch",
    "CheckBox": "Toggle", "MaterialCheckBox": "Toggle", "RadioButton": "Toggle", "MaterialRadioButton": "Toggle",
    "ToggleButton": "Toggle", "CheckedTextView": "Toggle", "Chip": "Toggle",
    "SeekBar": "Slider", "AppCompatSeekBar": "Slider", "RatingBar": "Slider", "Slider": "Slider",
    "Button": "Button", "ImageButton": "Button", "MaterialButton": "Button", "AppCompatButton": "Button",
    "AppCompatImageButton": "Button", "FloatingActionButton": "Button", "ExtendedFloatingActionButton": "Button",
    "TextView": "StaticText", "AppCompatTextView": "StaticText", "MaterialTextView": "StaticText",
    "ImageView": "Image", "AppCompatImageView": "Image",
    "RecyclerView": "ScrollView", "ListView": "ScrollView", "GridView": "ScrollView", "ScrollView": "ScrollView",
    "NestedScrollView": "ScrollView", "HorizontalScrollView": "ScrollView", "ViewPager": "ScrollView",
    "Toolbar": "NavigationBar", "MaterialToolbar": "NavigationBar", "ActionBarView": "NavigationBar",
    "BottomNavigationView": "TabBar", "NavigationBarView": "TabBar", "TabLayout": "TabBar",
    "TabView": "Tab", "WebView": "WebView",
}  # fmt: skip
#: The roles kept whatever they say: what can be acted on, and what covers.
ACTIONABLE = frozenset({"Button", "TextField", "SecureTextField", "SearchField", "Switch", "Toggle", "Slider", "Tab"})
COVERS = frozenset({"NavigationBar", "TabBar"})
FIELDS = frozenset({"TextField", "SecureTextField", "SearchField"})
#: The windows drawn over an app, by their type, as the role each covers with and what a line calls it.
WINDOW_COVERS: Mapping[str, tuple[str, str]] = {
    "INPUT_METHOD": ("Keyboard", "Keyboard"),
    "STATUS_BAR": ("StatusBar", "Status bar"),
    "NAVIGATION_BAR": ("Toolbar", "System navigation"),
}
#: The longest label made from what a container holds.
LABEL_MAX = 200


@dataclass(frozen=True)
class Box:
    """A rectangle, in pixels."""

    left: int
    top: int
    right: int
    bottom: int

    @property
    def empty(self) -> bool:
        return self.right <= self.left or self.bottom <= self.top

    def frame(self, scale: float) -> dict[str, float]:
        return {
            "x": round(self.left / scale, 2),
            "y": round(self.top / scale, 2),
            "width": round((self.right - self.left) / scale, 2),
            "height": round((self.bottom - self.top) / scale, 2),
        }


def _box(bounds: str) -> Box:
    """A view's bounds; an empty box when it has none. A view scrolled partly away can have its corners crossed."""
    found = _BOUNDS.match(bounds)
    if found is None:
        return Box(0, 0, 0, 0)
    left, top, right, bottom = (int(value) for value in found.groups())
    return Box(min(left, right), min(top, bottom), max(left, right), max(top, bottom))


def role_of(node: ElementTree.Element) -> str:
    """A view's role, from its class -- and from whether it can be tapped, for a class this does not know."""
    name = (node.get("class") or "").rsplit(".", 1)[-1].rsplit("$", 1)[-1]
    role = ROLES.get(name)
    tappable = node.get("clickable") == "true" or node.get("long-clickable") == "true"
    if role == "TextField" and node.get("password") == "true":
        return "SecureTextField"
    if role in (None, "StaticText", "Image") and tappable:
        return "Button"
    if role is None and node.get("checkable") == "true":
        return "Toggle"
    return role or ("ScrollView" if node.get("scrollable") == "true" else "Group")


def _words(node: ElementTree.Element) -> str | None:
    said = (node.get("content-desc") or "").strip() or (node.get("text") or "").strip()
    return said or None


def _held_words(node: ElementTree.Element) -> list[str]:
    return [said for child in node.iter("node") if child is not node and (said := _words(child))]


def _field_label(node: ElementTree.Element) -> str | None:
    """A field's name: what it describes itself as, else its hint; what is typed in it is its value."""
    return (node.get("content-desc") or "").strip() or (node.get("hint") or "").strip() or None


def _identifier(node: ElementTree.Element) -> str | None:
    """A view's resource id, without its package: ``title``, ``search_src_text``."""
    raw = node.get("resource-id") or ""
    return raw.rpartition(":id/")[2] or None


def _element(node: ElementTree.Element, scale: float, absorbed: bool) -> list[dict[str, Any]]:
    """A view as the document's elements: itself when it is worth reading, with what it holds, else what it holds."""
    role = role_of(node)
    words = _words(node)
    label = words
    absorbs = role == "Button" and words is None and not absorbed
    if absorbs:
        held = ", ".join(_held_words(node))
        label = held[:LABEL_MAX] or None
    children = [element for child in node.findall("node") for element in _element(child, scale, absorbed or absorbs)]
    box = _box(node.get("bounds") or "")
    worth = role in ACTIONABLE or role in COVERS or (words is not None and not absorbed)
    if box.empty or not worth:
        return children
    checked = node.get("checked") == "true"
    traits = [trait for trait, on in (
        ("Selected", node.get("selected") == "true" or checked),
        ("IsEditing", role in FIELDS and node.get("focused") == "true"),
    ) if on]  # fmt: skip
    value = None
    if role in ("Switch", "Toggle") and node.get("checkable") == "true":
        value = "1" if checked else "0"
    elif role in FIELDS:
        # An empty field says its hint as its text: that is not what was typed in it.
        typed = node.get("text") or None
        value = typed if typed != (node.get("hint") or None) else None
    return [
        {
            "type": role,
            "subrole": None,
            "label": _field_label(node) if role in FIELDS else label,
            "title": None,
            "identifier": _identifier(node),
            "value": value,
            "frame": box.frame(scale),
            "traits": traits,
            "enabled": node.get("enabled") != "false",
            "children": children,
        }
    ]


def window_covers(dumpsys: str) -> list[tuple[str, str, Box]]:
    """The windows drawn over the app now -- the keyboard, the status bar, the system navigation bar -- each as the
    role it covers with, what a line calls it, and where it is: the keyboard where it takes touches."""
    starts = list(_WINDOW.finditer(dumpsys))
    found = []
    for at, start in enumerate(starts):
        block = dumpsys[start.end() : starts[at + 1].start() if at + 1 < len(starts) else len(dumpsys)]
        kind = _TYPE.search(block)
        cover = WINDOW_COVERS.get(kind.group(1)) if kind else None
        if cover is None or "isVisible=true" not in block:
            continue
        where = _TOUCHABLE.search(block) if cover[0] == "Keyboard" else None
        frame = where or _FRAME.search(block)
        if frame is None:
            continue
        box = Box(*(int(value) for value in frame.groups()))
        if not box.empty:
            found.append((cover[0], cover[1], box))
    return found


def document(dump: str, windows: str, screen: Screen) -> dict[str, Any]:
    """A UI dump and what covers it, as the consolidated accessibility document, in the interface's points."""
    end = dump.find(END)
    start = dump.find("<?xml")
    if end < 0:
        said = dump.strip().splitlines()[-1] if dump.strip() else "it said nothing"
        raise ConnectorError(f"the device's screen could not be read through uiautomator now: {said}")
    try:
        root = ElementTree.fromstring(dump[max(start, 0) : end + len(END)])
    except ElementTree.ParseError as exc:
        raise ConnectorError("the device's UI dump is not XML") from exc
    rotation = root.get("rotation") or "0"
    orientation = orientation_of(int(rotation) if rotation.isdigit() else 0)
    sideways = orientation in ("landscape_left", "landscape_right")
    width, height = (screen.height_pt, screen.width_pt) if sideways else (screen.width_pt, screen.height_pt)
    elements = [element for node in root.findall("node") for element in _element(node, screen.scale, False)]
    elements += [
        {
            "type": role, "subrole": None, "label": label, "title": None, "identifier": None, "value": None,
            "frame": box.frame(screen.scale), "traits": [], "enabled": True, "children": [],
        }
        for role, label, box in window_covers(windows)
    ]  # fmt: skip
    return {
        "backend": "uiautomator",
        "elements": elements,
        "modal": None,
        "screen": {"coordinate_space": "screen", "width": width, "height": height},
        "truncated": False,
        "orientation": orientation,
    }


class UiAutomatorReader:
    """An Android device's screen, read through uiautomator and its window list."""

    def __init__(self, adb: Adb, serial: str, screen: Screen) -> None:
        self._adb = adb
        self._serial = serial
        self._screen = screen

    async def accessibility(self) -> dict[str, Any]:
        try:
            dump, windows = await asyncio.gather(
                self._adb.exec_out(self._serial, ["uiautomator", "dump", "/dev/tty"], timeout=DUMP_TIMEOUT_S),
                self._adb.exec_out(self._serial, ["dumpsys", "window", "windows"]),
            )
        except (AdbError, asyncio.TimeoutError, TimeoutError) as exc:
            raise ConnectorError(f"the device's screen could not be read through uiautomator now: {exc}") from exc
        return document(dump.decode(errors="replace"), windows.decode(errors="replace"), self._screen)
