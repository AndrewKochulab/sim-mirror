# SPDX-License-Identifier: Apache-2.0
"""Xcode's UI hierarchy of a screen, as mcpbridge writes it, read into the document a companion answers with.

A capture writes the hierarchy as text, one element a line, indented one space a level under each application:

    Application bundle identifier: com.apple.Preferences
    Application, pid: 65688, label: 'Settings'
     Window, {{0.0, 0.0}, {402.0, 874.0}}, hitPoint: {201.0, 437.0}
          Button, {{16.0, 380.3}, {370.0, 52.0}}, identifier: 'com.apple.settings.general', label: 'General', ...

Measured on Xcode 27.0 with iOS 27.0 (2026-09-16), the text is not escaped: a label keeps its quotes and its line
breaks as they are, so ``label: 'It's a 'quote''`` and a label running onto the next line are both written. So each
line is read from its ends inward -- the frame from the front; `hitPoint`, an ``activationBundleId`` and the known
flags from the back -- and a quoted value ends at the last place the next key could start. A label an app words to
look like more keys can still be read wrong; it only ever misnames that element, since what is on screen is the app's
to say anyway. A ``value`` is written unquoted, and Xcode cuts a long one short with ``...``.

What comes out is what `perception.readers.tree_from_document` reads: element types in the vocabulary snapshots use
(an app ``Icon`` is a button), a disabled element not enabled, ``Keyboard Focused`` as editing, a placeholder as the
title, and an alert or a sheet as the modal in front.
"""

from __future__ import annotations

import math
import re
from typing import Any

from sim_mirror.perception.snapshot import CONTAINERS

#: The most elements read from one capture; the rest are left out and the tree says it was cut short.
ELEMENTS_MAX = 5000
#: Keys an element line may have after its frame, in the order Xcode writes them: quoted, then the value, unquoted.
KEYS = ("identifier", "label", "placeholderValue", "value")
QUOTED = KEYS[:-1]
#: Flags Xcode writes after the keys, longest first so ``Keyboard Focused`` is not read as ``Focused``.
FLAGS = ("Keyboard Focused", "isRemoteLeafPlaceholder", "Selected", "Disabled", "Focused")
#: How a flag reads as a companion's trait.
TRAITS = {"Keyboard Focused": "IsEditing", "Selected": "Selected", "Focused": "Focused"}
#: Element types that read as another snapshots already know.
ROLES = {"Icon": "Button", "TextView": "TextField", "Alert": "Group", "Sheet": "Group", "StatusBar": "Group",
         "WebView": "Group"}  # fmt: skip
#: How Xcode ends a value it cut short.
CUT = "..."
KEYBOARD = "Keyboard"
#: Element types that are in front of the rest of the screen.
MODALS = frozenset({"Alert", "Sheet"})

_ELEMENT = re.compile(r"\A(?P<indent> *)(?P<type>[A-Z][A-Za-z]*), (?P<rest>(?:\{\{|pid: \d).*)\Z", re.S)
_NUMBER = r"-?(?:\d+(?:\.\d+)?|inf|nan)"
_FRAME = re.compile(
    rf"\A\{{\{{(?P<x>{_NUMBER}), (?P<y>{_NUMBER})\}}, \{{(?P<width>{_NUMBER}), (?P<height>{_NUMBER})\}}\}}(?:, |\Z)"
)
_PID = re.compile(r"\Apid: \d+(?:, |\Z)")
_HIT_POINT = re.compile(rf"(?:\A|, )hitPoint: \{{{_NUMBER}, {_NUMBER}\}}\Z")
_ACTIVATION = re.compile(r"(?:\A|, )activationBundleId: [^\s,']+\Z")
_HEADER = re.compile(r"\A(?:-{8,}|[A-Za-z ]+: .*)\Z")


def _frame(match: re.Match[str]) -> dict[str, float] | None:
    values = {name: float(match[name]) for name in ("x", "y", "width", "height")}
    return values if all(math.isfinite(value) for value in values.values()) else None


def _strip_end(rest: str, pattern: re.Pattern[str]) -> str:
    found = pattern.search(rest)
    return rest[: found.start()] if found else rest


def _flags(rest: str) -> tuple[str, list[str]]:
    """The line without the flags at its end, and those flags in the order written."""
    found: list[str] = []
    stripped = True
    while stripped:
        stripped = False
        for flag in FLAGS:
            if rest == flag or rest.endswith(", " + flag):
                found.insert(0, flag)
                rest = rest[: -len(flag)].removesuffix(", ")
                stripped = True
                break
    return rest, found


def _keys(rest: str) -> dict[str, str]:
    """The keys of what is left of a line: ``identifier: '…', label: '…', placeholderValue: '…', value: …``."""
    found: dict[str, str] = {}
    for index, key in enumerate(QUOTED):
        prefix = f"{key}: '"
        if not rest.startswith(prefix):
            continue
        body = rest[len(prefix) :]
        end = -1
        for later in KEYS[index + 1 :]:
            anchor = f"', {later}: " + ("" if later == "value" else "'")
            # An identifier is the developer's and short: it ends at the first key after it. Text a person reads
            # ends at the last, so quotes and commas inside it stay in it.
            end = body.find(anchor) if key == "identifier" else body.rfind(anchor)
            if end >= 0:
                break
        if end >= 0:
            found[key], rest = body[:end], body[end + 3 :]
        else:
            found[key], rest = body.removesuffix("'"), ""
    if rest.startswith("value: "):
        found["value"] = rest[len("value: ") :]
    return found


def _element(kind: str, rest: str) -> dict[str, Any]:
    frame = None
    placed = _FRAME.match(rest) or _PID.match(rest)
    if placed:
        frame = _frame(placed) if placed.re is _FRAME else None
        rest = rest[placed.end() :]
    rest = _strip_end(_strip_end(rest, _ACTIVATION), _HIT_POINT)
    rest, flags = _flags(rest)
    keys = _keys(rest)
    label, value, identifier = keys.get("label", ""), keys.get("value", ""), keys.get("identifier", "")
    if value.endswith(CUT) and label.startswith(value.removesuffix(CUT)):
        value = ""  # web text says itself twice, the second time cut short
    if kind == "Image" and label == identifier:
        label = ""  # the symbol's name, such as chevron.forward: nothing a person reads
    return {
        "type": ROLES.get(kind, kind),
        "label": label,
        "identifier": identifier,
        "title": keys.get("placeholderValue", ""),
        "value": value,
        "frame": frame,
        "enabled": "Disabled" not in flags,
        "traits": [TRAITS[flag] for flag in flags if flag in TRAITS],
        "children": [],
    }


def _ended(rest: str) -> bool:
    """Whether an element's line reads as finished: at its hit point, a quote, or a flag."""
    return rest.endswith(("}", "'", *FLAGS))


def _entries(text: str) -> list[tuple[int, str, str]]:
    """Each element's depth, type and the rest of its line -- with any lines its label ran onto."""
    entries: list[tuple[int, str, str]] = []
    for line in text.splitlines():
        element = _ELEMENT.match(line)
        if element:
            entries.append((len(element["indent"]), element["type"], element["rest"]))
            continue
        between = not line.strip() or _HEADER.match(line)
        if entries and not (between and _ended(entries[-1][2])):
            depth, kind, rest = entries[-1]
            entries[-1] = (depth, kind, f"{rest}\n{line}")
    return entries


def _repeats(element: dict[str, Any], speaker: dict[str, Any] | None) -> bool:
    """Whether an element only says again what the element around it says: the text inside a button, the switch
    inside a labelled switch."""
    if speaker is None:
        return False
    label = element["label"].casefold()
    if label:
        return label in speaker["label"].casefold()
    return element["type"] == speaker["type"] and not element["identifier"]


def _covered(frame: dict[str, float] | None, keyboards: list[dict[str, float]]) -> bool:
    if frame is None:
        return False
    x, y = frame["x"] + frame["width"] / 2, frame["y"] + frame["height"] / 2
    return any(k["x"] <= x <= k["x"] + k["width"] and k["y"] <= y <= k["y"] + k["height"] for k in keyboards)


def _uncovered(elements: list[dict[str, Any]], keyboards: list[dict[str, float]]) -> list[dict[str, Any]]:
    """The elements not hidden behind the keyboard. Xcode lists what the keyboard covers; a tap there hits a key."""
    kept: list[dict[str, Any]] = []
    for element in elements:
        if element["type"] == KEYBOARD:
            kept.append(element)
            continue
        element["children"] = _uncovered(element["children"], keyboards)
        if element["type"] not in CONTAINERS and _covered(element["frame"], keyboards):
            kept.extend(element["children"])
        else:
            kept.append(element)
    return kept


def _all(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [each for element in elements for each in (element, *_all(element["children"]))]


def document_from_hierarchy(text: str) -> dict[str, Any]:
    """A capture's hierarchy text as a companion's accessibility document: ``elements``, ``modal``, ``truncated``.

    An element that only repeats the element around it, or one written twice in the same place, is left out; what
    it held stays, under the element above it. So is one the keyboard covers.
    """
    roots: list[dict[str, Any]] = []
    #: Each open element's depth, the list its children go into, and the nearest element around it that says something.
    stack: list[tuple[int, list[dict[str, Any]], dict[str, Any] | None]] = []
    seen: set[tuple[Any, ...]] = set()
    modal: dict[str, str] | None = None
    entries = _entries(text)
    for depth, kind, rest in entries[:ELEMENTS_MAX]:
        element = _element(kind, rest)
        if modal is None and kind in MODALS:
            modal = {"type": kind, "label": element["label"]}
        while stack and stack[-1][0] >= depth:
            stack.pop()
        siblings, speaker = (stack[-1][1], stack[-1][2]) if stack else (roots, None)
        frame = element["frame"]
        place = (element["type"], element["label"], element["identifier"], *(frame or {}).values())
        if (element["label"] and place in seen) or _repeats(element, speaker):
            stack.append((depth, siblings, speaker))
            continue
        seen.add(place)
        siblings.append(element)
        says = element["type"] not in CONTAINERS and bool(element["label"])
        stack.append((depth, element["children"], element if says else speaker))
    keyboards = [element["frame"] for element in _all(roots) if element["type"] == KEYBOARD and element["frame"]]
    if keyboards:
        roots = _uncovered(roots, keyboards)
    return {"elements": roots, "modal": modal, "truncated": len(entries) > ELEMENTS_MAX}
