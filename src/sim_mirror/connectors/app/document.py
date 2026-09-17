# SPDX-License-Identifier: Apache-2.0
"""An app's hierarchy, read in the shape a connector's accessibility document has.

The app says what each view is in a snapshot's words (``button``, ``secure``, ``cell``) and SimMirror's readers speak
accessibility's (``Button``, ``SecureTextField``, ``Cell``), so the hierarchy becomes a document
`perception.readers.tree_from_document` reads -- the way Xcode's hierarchy does -- and everything after it is shared.

On the way, what cannot help an agent is left out: a view with no size (what it holds stays), a container that says
nothing and cannot be touched (what it holds takes its place), and what is under the software keyboard, where a tap
lands on a key. It is read leniently -- a kind or a field SimMirror does not know is not a reason to refuse the rest --
but within a depth and a count, and only from the app that listed the port it answered on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sim_mirror.connectors.app import wire
from sim_mirror.connectors.app.errors import AppSdkError
from sim_mirror.connectors.base import Screen
from sim_mirror.perception.model import NAMED
from sim_mirror.perception.snapshot import CONTAINERS

#: A node's kind as accessibility would say its role.
ROLES = {
    "button": "Button", "link": "Link", "text": "StaticText", "heading": "Heading", "image": "Image",
    "field": "TextField", "secure": "SecureTextField", "search": "SearchField", "switch": "Switch",
    "slider": "Slider", "stepper": "Stepper", "picker": "Picker", "segments": "SegmentedControl", "tab": "Tab",
    "cell": "Cell", "container": "Other", "scroll": "ScrollView", "list": "Table", "navigation_bar": "NavigationBar",
    "tab_bar": "TabBar", "toolbar": "Toolbar",
}  # fmt: skip
#: A node's traits as accessibility would say them.
TRAITS = {"selected": "Selected", "editing": "IsEditing"}
#: How a modal's kind reads as the type of an accessibility document's modal.
MODALS = {"alert": "Alert", "sheet": "Sheet", "full_screen": "FullScreen", "popover": "Popover"}
#: The fields a node is read from: what the reader relies on of `protocol/app-sdk/v1`'s Node.
NODE_FIELDS = frozenset(
    {"kind", "label", "label_source", "identifier", "value", "placeholder", "frame", "traits", "enabled", "interactive",
     "children"}
)  # fmt: skip
#: The fields a hierarchy is read from.
HIERARCHY_FIELDS = frozenset({"protocol", "sdk_version", "app", "screen", "modal", "keyboard", "windows", "truncated",
                              "notes"})  # fmt: skip

_SDK_VERSION = re.compile(r"\A[0-9A-Za-z.+-]{1,32}\Z")


@dataclass(frozen=True)
class SharedApp:
    """An app sharing its view hierarchy: what a person is told of it."""

    name: str
    bundle_id: str
    sdk_version: str


@dataclass(frozen=True)
class AppDocument:
    """An app's hierarchy as an accessibility document, and what else it said."""

    document: dict[str, Any]
    app: SharedApp
    screen: Screen | None
    protocol: int
    #: Whether the app, or SimMirror reading it, left views out.
    truncated: bool
    #: How many elements the document holds.
    elements: int
    notes: tuple[str, ...] = ()


def _text(value: Any, limit: int | None = None) -> str:
    text = value if isinstance(value, str) else ""
    return text[:limit] if limit is not None else text


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if number == number and abs(number) != float("inf") else None


def _frame(raw: Any) -> dict[str, float] | None:
    if not isinstance(raw, dict):
        return None
    values = [_number(raw.get(key)) for key in ("x", "y", "width", "height")]
    if any(value is None for value in values):
        return None
    x, y, width, height = (value for value in values if value is not None)
    return {"x": x, "y": y, "width": width, "height": height}


def _covered(frame: dict[str, float], keyboard: dict[str, float] | None) -> bool:
    """Whether the middle of a frame is on the keyboard."""
    if keyboard is None:
        return False
    x, y = frame["x"] + frame["width"] / 2, frame["y"] + frame["height"] / 2
    return (
        keyboard["x"] <= x <= keyboard["x"] + keyboard["width"]
        and keyboard["y"] <= y <= keyboard["y"] + keyboard["height"]
    )


def _traits(raw: Any) -> list[str]:
    return (
        [TRAITS[trait] for trait in raw if isinstance(trait, str) and trait in TRAITS] if isinstance(raw, list) else []
    )


def _role(kind: str, labeled: bool, interactive: bool) -> str:
    if kind == "container" and labeled and interactive:
        return "Button"
    return ROLES.get(kind, "")


class _Reading:
    """One hierarchy being read: the keyboard it hides things under, and how much of it is left to read."""

    def __init__(self, keyboard: dict[str, float] | None, max_nodes: int) -> None:
        self.keyboard = keyboard
        self.left = max_nodes
        self.truncated = False

    def nodes(self, raw: Any, depth: int) -> list[dict[str, Any]]:
        if not isinstance(raw, list):
            return []
        if depth > wire.DEPTH_MAX:
            self.truncated = self.truncated or bool(raw)
            return []
        kept: list[dict[str, Any]] = []
        for node in raw:
            if isinstance(node, dict):
                kept.extend(self.node(node, depth))
        return kept

    def node(self, raw: dict[str, Any], depth: int) -> list[dict[str, Any]]:
        """A node as the elements it reads as: itself holding its children, or only its children in its place."""
        if self.left <= 0:
            self.truncated = True
            return []
        frame = _frame(raw.get("frame"))
        kind, label = _text(raw.get("kind")), _text(raw.get("label"))
        interactive = raw.get("interactive") is True
        role = _role(kind, bool(label), interactive)
        value = "" if kind == "secure" else _text(raw.get("value"))
        says = bool(label or _text(raw.get("identifier")))
        hollow = (role in CONTAINERS or not role) and not says and not interactive
        if frame is None or frame["width"] <= 0 or frame["height"] <= 0 or hollow:
            return self.nodes(raw.get("children"), depth + 1)
        if role not in CONTAINERS and _covered(frame, self.keyboard):
            return self.nodes(raw.get("children"), depth + 1)
        self.left -= 1
        return [
            {
                "type": role,
                "label": label,
                "title": _text(raw.get("placeholder")),
                "identifier": _text(raw.get("identifier")),
                "value": value,
                "frame": frame,
                "traits": [
                    *_traits(raw.get("traits")),
                    *([NAMED] if label and raw.get("label_source") == "tag" else []),
                ],
                "enabled": raw.get("enabled") is not False,
                "children": self.nodes(raw.get("children"), depth + 1),
            }
        ]


def _screen(raw: Any) -> Screen | None:
    if not isinstance(raw, dict):
        return None
    width, height, scale = (_number(raw.get(key)) for key in ("width_pt", "height_pt", "scale"))
    if not width or not height or not scale or width < 0 or height < 0 or scale < 0:
        return None
    return Screen(round(width * scale), round(height * scale), round(width), round(height), scale)


def _count(elements: list[dict[str, Any]]) -> int:
    return sum(1 + _count(element["children"]) for element in elements)


def document_from_app(raw: Any, *, bundle_id: str, pid: int, max_nodes: int) -> AppDocument:
    """What an app answered as an accessibility document, if it is the app that listed the port it answered on.

    At most `max_nodes` elements are kept, however many the app sent.
    """
    if not isinstance(raw, dict):
        raise AppSdkError("the app answered with something other than a view hierarchy")
    app = raw.get("app")
    if not isinstance(app, dict) or app.get("bundle_id") != bundle_id or app.get("pid") != pid:
        raise AppSdkError(f"what answered for {bundle_id} is not the app that said it listens there")
    raw_version = _text(raw.get("sdk_version"))
    protocol = raw.get("protocol")
    raw_keyboard = raw.get("keyboard")
    keyboard = _frame(raw_keyboard.get("frame")) if isinstance(raw_keyboard, dict) else None
    reading = _Reading(keyboard, max_nodes)
    elements: list[dict[str, Any]] = []
    windows = raw.get("windows")
    for window in windows if isinstance(windows, list) else []:
        if isinstance(window, dict):
            elements.extend(reading.nodes(window.get("nodes"), 1))
    raw_modal = raw.get("modal")
    modal = None
    if isinstance(raw_modal, dict):
        modal = {"type": MODALS.get(_text(raw_modal.get("kind")), "Modal"), "label": _text(raw_modal.get("name"))}
    notes = raw.get("notes")
    name = _text(app.get("name"), wire.NAME_MAX) or bundle_id
    return AppDocument(
        document={"elements": elements, "modal": modal, "truncated": False},
        app=SharedApp(
            name=name,
            bundle_id=bundle_id,
            sdk_version=raw_version if _SDK_VERSION.match(raw_version) else "unknown",
        ),
        screen=_screen(raw.get("screen")),
        protocol=protocol if isinstance(protocol, int) and not isinstance(protocol, bool) else 0,
        truncated=raw.get("truncated") is True or reading.truncated,
        elements=_count(elements),
        notes=tuple(
            _text(note, wire.NOTE_MAX) for note in (notes if isinstance(notes, list) else []) if isinstance(note, str)
        )[: wire.NOTES_MAX],
    )
