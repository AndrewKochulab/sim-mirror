# SPDX-License-Identifier: Apache-2.0
"""What an agent is doing, in a few words a person watching reads: ``Tapping “Add”``, ``Reading the screen``,
``Building``.

A viewer shows it beside the agent's pointer and its name, and the macOS overlay beside the pointer it draws over the
app. Every phrase is made here, from what SimMirror itself is about to do -- a gesture it plays, a tool call it runs, a
wait it waits -- so every place that shows one says the same. It is a person's words: brief, never parsed back.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

#: The most characters of an element's label, typed text or other name a phrase quotes; longer ones are cut with "…".
QUOTED_MAX = 24

Point = tuple[float, float]

_GESTURES = {
    "tap": "Tapping",
    "long_press": "Pressing and holding",
    "drag": "Dragging",
    "scroll": "Scrolling",
    "right_click": "Right-clicking",
    "hover": "Pointing at",
}
_CLICKS = {"tap": "Clicking", "long_press": "Holding a click on"}
_VERBS = {
    "launch": "Launching",
    "relaunch": "Relaunching",
    "quit": "Quitting",
    "install": "Installing",
    "open": "Opening",
    "switched": "Switching",
    "turned": "Turning",
}
_SNAPSHOT = "Reading the screen"
_CALLS: dict[str, str | Mapping[str, str]] = {
    "sim_snapshot": _SNAPSHOT,
    "sim_screenshot": "Looking at the screen",
    "sim_build_run": "Building",
    "sim_test": "Running tests",
    "sim_record": {"start": "Recording the screen", "stop": "Saving the recording", "": "Checking recordings"},
    "sim_app": {
        "launch": "Launching the app",
        "terminate": "Quitting the app",
        "install": "Installing the app",
        "open_url": "Opening a link",
        "logs": "Reading the logs",
        "": "Checking the app",
    },
    "sim_device": {
        "boot": "Starting the device",
        "restart": "Restarting the device",
        "choose": "Switching devices",
        "rotate": "Turning the device",
        "stop": "Letting the device go",
        "": "Checking the device",
    },
    # A batch's steps and its wait each say what they do as they do it.
    "sim_act": "",
}


def quoted(text: str) -> str:
    """`text` on one line in quotation marks, cut to `QUOTED_MAX` characters."""
    flat = " ".join(text.split())
    if len(flat) > QUOTED_MAX:
        flat = flat[: QUOTED_MAX - 1].rstrip() + "…"
    return f"“{flat}”"


def _direction(points: Sequence[Point]) -> str:
    """Which way a swipe from its first point to its last goes on the screen, whose y grows downwards."""
    (x0, y0), (x1, y1) = points[0], points[-1]
    dx, dy = x1 - x0, y1 - y0
    if abs(dy) >= abs(dx):
        return "up" if dy < 0 else "down"
    return "left" if dx < 0 else "right"


def _named(verb: str, label: str) -> str:
    return f"{verb} {quoted(label)}" if label else verb


def of_gesture(
    kind: str, *, label: str = "", caption: str = "", points: Sequence[Point] = (), mac: bool = False
) -> str:
    """What playing a gesture of `kind` is, said with the element it lands on (`label`), what it types or presses
    (`caption`) and, for a swipe, which way it goes on the screen (`points`). A macOS app is clicked, not tapped."""
    if kind in _GESTURES:
        verb = _CLICKS.get(kind, _GESTURES[kind]) if mac else _GESTURES[kind]
        return _named(verb, label)
    if kind == "swipe":
        return f"Swiping {_direction(points)}" if len(points) > 1 else "Swiping"
    if kind == "type":
        return f"Typing {quoted(caption)}" if caption.strip() else "Typing…"
    if kind in ("press", "keys"):
        return f"Pressing {caption}" if caption else "Pressing a key"
    if kind == "menu":
        return f"Choosing {caption}" if caption else "Choosing from the menu"
    if kind == "look":
        return caption or _SNAPSHOT
    if kind == "pause":
        return "Waiting"
    if caption:
        verb, _, rest = caption.partition(" ")
        if verb in _VERBS:
            return f"{_VERBS[verb]} {rest}".strip()
        return caption[0].upper() + caption[1:]
    return quoted(label) if label else kind.replace("_", " ").capitalize()


def of_call(name: object, arguments: object) -> str:
    """What a tool call does while it runs, with nothing more particular to say: ``""`` for a call whose parts say
    for themselves (`sim_act`), and for one this does not know."""
    said = _CALLS.get(name, "") if isinstance(name, str) else ""
    if isinstance(said, str):
        return said
    action = arguments.get("action") if isinstance(arguments, Mapping) else None
    return said.get(action, said[""]) if isinstance(action, str) else said[""]


def of_wait(spec: object) -> str:
    """What a batch's wait waits for, from the ``wait`` it asked (`perception.wait.parse_wait` has checked it)."""
    if not isinstance(spec, Mapping):
        return "Waiting"
    if "settle_ms" in spec:
        return "Waiting for the screen to settle"
    stable = spec.get("stable")
    if isinstance(stable, Mapping):
        return _waiting(stable, "Waiting for {} to stay", "Waiting for {} to stay away")
    return _waiting(spec, "Waiting for {}", "Waiting for {} to go")


def _waiting(spec: Mapping[str, object], present: str, gone: str) -> str:
    key, phrase = ("for", present) if "for" in spec else ("gone", gone)
    return phrase.format(quoted(_wanted(spec.get(key))))


def _wanted(match: object) -> str:
    """The text a wait matches: given as itself, or as ``{"exact": …}``, ``{"regex": …}`` or ``{"text": …}``."""
    if isinstance(match, Mapping):
        match = next((match[key] for key in ("exact", "text", "regex") if isinstance(match.get(key), str)), "")
    return match if isinstance(match, str) and match.strip() else "text"
