# SPDX-License-Identifier: Apache-2.0
"""What an agent does to a device: steps played in order, and drawn on every screen watching it.

The agent tools come here for everything that looks at or touches a device. A call is a batch of steps -- tap, long
press, swipe, drag, type, press, pause -- because a round trip is what an agent pays for, not a gesture. After the
steps it can wait, for text to appear or go or for the screen to settle after an animation (`perception.wait`), and it
answers with what changed on screen (`perception.snapshot`).

**Every gesture is announced before it lands.** The device's `EventBus` carries an agent intent with the gesture's
points as shares of the screen, so a viewer's pointer glides there; while anyone is watching, the gesture then waits
``agent.cursor_lead_ms`` for the pointer to arrive. Nobody watching, nothing waits.

**The pointer stays while the agent works.** A tool call tells the screens the agent is working when it starts and
every `WORKING_EVERY_S` while it runs -- a build or a test run can take minutes with nothing to draw -- and that it
has ended when it ends; and every event carries ``agent.cursor_linger_s``: how long after the last one a viewer keeps
the pointer, resting where the agent last acted. The pointer is the viewer's, drawn over the screen: nothing here puts
it in a frame, so a screenshot or a recording of the device never shows it.

**A person comes first.** Agents wait for a person's hand to have been still for
`QUIET_S`, giving up after `PERSON_WAIT_S` with a reason. A person never waits for an agent. While tests run on the
device (`instance.busy`) no agent gesture is played at all. What "a person's hand" means is input through a viewer:
a finger on a real phone's own glass reaches no viewer and nothing here sees it, so it is not waited for. A host that
knows when its person has the phone in hand says so through `InputPause`: while it answers paused, a batch waits
`PERSON_WAIT_S` for it to lift and then refuses, and a batch under way stops before its next gesture.

**Refs are read against the screen as it is.** Each step looks its ref up in the snapshot this agent last took, and
reads the screen again when the ref is not in it, or when the screen may have changed since: after a step of this
call, and -- for a step that touches -- after a pause, when that snapshot was taken more than `FRESH_S` before the call
began, or when another agent has acted on the device since. The screen may have changed out of this agent's sight
then, so a ref whose element moved or changed is refused with what it is now, and the agent looks again -- an alert
that came up is never tapped through a ref read before it. ``allow_stale`` skips that check. A ref whose element is
gone is an error that says to look again -- never a tap on whatever is there now. Where the snapshot cannot tell
whether a touch on it lands on it -- it reads as under a bar, or sits where a navigation bar nobody listed would be --
the device is asked what is at its point first (`perception.in_front`).

**A batch is checked whole before any of it plays**: a step asked for wrongly -- a pause too long, a press of no key
-- refuses the call with nothing played. A step that cannot be played -- its ref is not on screen -- ends the batch;
the steps before it stay done, and the answer says which, each with when it was done since the call began.

**The device is held for the steps, not the wait.** Agents take turns for their steps (`instance.input_lock`); the wait
after them only reads, so another agent's steps may play during it. A call runs `CALL_S` at most.

**A screen accessibility says nothing about is read from its pixels.** Snapshots read the connector's tree, with any
reader merged in, and -- as the scope's ``perception.ocr`` asks -- the text in the screen's pixels when that tree says
nothing, on every snapshot, or never (`perception.readers.compose`). A device whose connector cannot read a tree at all
is still read from its pixels, so its agents are offered snapshots (`capabilities`).

**What the connector cannot do is said plainly.** A device mirrored by a connector that cannot read or touch the screen
refuses snapshots and steps with which connector could.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import math
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Collection
from dataclasses import dataclass, field, replace
from typing import Any, TypeVar

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import Capability, ConnectorError, Crop, InputSink, Shot, button_refusal
from sim_mirror.core import gestures
from sim_mirror.core.instance import DeviceInstance
from sim_mirror.core.manager import DeviceManager
from sim_mirror.core.refusals import ObserveGuard, touch_refusal
from sim_mirror.core.text_entry import paste_refused, text_entry
from sim_mirror.core.text_overlay import STILL
from sim_mirror.host_copy import HostCopy
from sim_mirror.perception import in_front
from sim_mirror.perception.ocr import NoRecognizer, OcrReaders, OnRead, RecognizedLine
from sim_mirror.perception.orientation import Orientation
from sim_mirror.perception.readers import (
    DocumentReader,
    ExtraReaders,
    NoExtraReaders,
    TreeReader,
    compose,
    node_from,
)
from sim_mirror.perception.settle import ScreenshotSettle, stillness_for
from sim_mirror.perception.snapshot import ENTRY, Element, Snapshot, build, diff, element_of
from sim_mirror.perception.wait import TIMEOUT_MS, Waited, Waiter, parse_wait
from sim_mirror.platform.errors import DeviceControlError
from sim_mirror.protocol import PANEL_BUTTONS, WORKING_EVERY_S, Agent, agent_done, agent_intent, agent_working
from sim_mirror.seams import Caller, ConfigSource, HeldDevice, InputPause
from sim_mirror.validation import Invalid, is_number, whole

T = TypeVar("T")

STEP_KINDS = ("tap", "long_press", "swipe", "drag", "type", "press", "pause")
#: What else each kind of step may say. Anything more is refused, never ignored: a key no step reads was once dropped
#: silently, and `{"type": "text", "text": "Groceries"}` typed the word "text".
STEP_OPTIONS: dict[str, tuple[str, ...]] = {
    "tap": (),
    "long_press": ("ms",),
    "swipe": (),
    "drag": (),
    "type": ("into", "clear", "submit"),
    "press": (),
    "pause": (),
}
#: What the object of a step that takes one may hold, refused past that the same way: a drag's ``hold_ms`` was dropped.
INNER_OPTIONS: dict[str, tuple[str, ...]] = {
    "swipe": ("from", "to", "direction", "ms"),
    "drag": ("path", "ms"),
}
MAX_STEPS = 20
TYPE_MAX = 2000
CAPTION_MAX = 80
DRAG_POINTS_MAX = 50
GESTURE_MS = (50, 5000)
LONG_PRESS_MS = 800
PAUSE_MS = (0, 30000)
#: How long a batch's pauses may add up to: a wait after the steps reads and holds nothing, so it is how a call waits
#: for longer.
PAUSES_MAX_MS = 120000
PAUSE_HINT = f'for longer, use wait {{"for": …, "timeout_ms": up to {TIMEOUT_MS[1]}}} after the steps'
#: How long one call may run: its steps, then its wait. A step not begun by then is not played, and the wait gives up.
CALL_S = 300.0
#: How old a snapshot may be when a call begins and its refs still be touched without reading the screen again.
FRESH_S = 2.0
#: How far a ref's element may move, in points, before a touch read against an older snapshot is refused.
MOVED_PT = 10.0
#: How far a swipe given only a direction goes, as a share of the screen.
SWIPE_SHARE = 0.35
#: How long after tapping a field the text goes into it, so the field has focus.
FOCUS_S = 0.35
QUIET_S = 0.3
PERSON_WAIT_S = 2.0
PERSON_POLL_S = 0.05
PRESSABLE = (*gestures.KEYS, *PANEL_BUTTONS)
DIRECTIONS = {"up": (0.0, -1.0), "down": (0.0, 1.0), "left": (-1.0, 0.0), "right": (1.0, 0.0)}
SCREENSHOT_WIDTH = (160, 1200)
SCREENSHOT_QUALITY = 70
FRAMES_MAX = 6
INTERVAL_MS = (50, 2000)
#: How far around an element a screenshot of it reaches, in points.
REGION_HALF = 60.0

Point = tuple[float, float]

#: How long a turn waits for the app on screen to be read lying the way the device was turned, and how often it reads
#: it meanwhile: an app that does not turn that way waits it out.
TURN_WAIT_S = 2.0
TURN_POLL_S = 0.2
#: How a turn then waits for the app to stop turning, as a wait after steps is asked for.
TURN_SETTLE = {"settle_ms": 300, "timeout_ms": 3000}
#: How a swipe or a drag is waited out before the screen is read after it, by a later step or the snapshot the call
#: answers with: a list flicked goes on gliding, a ref read meanwhile is where its row was passing, and on iOS a tap on
#: a list still moving only stops it -- on an iPhone 17 Pro a row read 0.8 s after a swipe and tapped was not opened.
MOVED_SETTLE = {"settle_ms": 300, "timeout_ms": 2500}
#: The gestures that set what is on the screen moving, to be waited out (`MOVED_SETTLE`).
MOVING = frozenset({"swipe", "drag"})

PERSON_USING = "the person watching is using the device; try again in a moment"
PAUSED = "a person has paused agents on this device; try again once they resume"


def _noted(instance: DeviceInstance) -> str:
    """Why a device's screen is read from its pixels alone -- its connector's note, as a line ``note: …`` -- or nothing
    when it is read otherwise or the connector says nothing: a real device that lost its cable says so here."""
    session = instance.session
    if session is None or session.reader is not None or not instance.fallback_reason:
        return ""
    return f"note: {instance.fallback_reason}"


class NoPause:
    """No pause: what a host that gives none has. Agents give way to a viewer's input only."""

    def paused(self, device: HeldDevice) -> bool:
        return False


class ActionError(Exception):
    """What an agent asked for that cannot be done, said so the agent knows what to do next."""


@dataclass(frozen=True)
class Gesture:
    kind: str
    summary: str
    events: tuple[gestures.Timed, ...] = ()
    points: tuple[Point, ...] = ()
    label: str = ""
    caption: str = ""
    #: Text that goes onto the device's pasteboard before the events play; "" when it is typed.
    text: str = ""
    pause_s: float = 0.0
    #: Text the session types whole once the events have played (`DeviceSession.text`), and the events after it.
    typed: str = ""
    after: tuple[gestures.Timed, ...] = ()


@dataclass(frozen=True)
class Turned:
    """A device turned: which way the app on screen was then read lying -- None when the screen could not be read --
    and what changed on screen, or why it could not be read."""

    orientation: Orientation | None
    changes: str


@dataclass
class _Memory:
    snapshot: Snapshot | None = None
    #: Whether a step of this agent's played since the snapshot was taken, so what it shows may have moved or gone --
    #: a keyboard opened over it, a screen pushed -- and a ref is read again before it is touched, where it now is.
    stale: bool = False
    #: Whether a pause of this agent's played since the snapshot, so the screen may have changed out of its sight. A
    #: touch reads it again, and refuses a ref whose element moved or changed.
    unseen: bool = False
    #: When the snapshot's read began, on the actions' clock.
    read_at: float = -math.inf
    #: How many gestures had been played on the device when the snapshot's read began (`AgentActions._unseen`): one
    #: played since -- by another agent, even while the read ran -- is out of this agent's sight, as a pause is.
    generation: int = 0
    #: When this agent's call under way began, and whether it touches refs as its snapshot has them (``allow_stale``).
    began: float = -math.inf
    allow_stale: bool = False
    ids: itertools.count[int] = field(default_factory=lambda: itertools.count(1))


@dataclass(frozen=True)
class Acted:
    """What a batch did: its answer, whether a step failed, and whether its wait was not met."""

    text: str
    failed: bool = False
    unmet: bool = False
    completed_steps: int = 0
    failed_step: int | None = None


def _whole(value: Any, default: int, bounds: tuple[int, int], name: str, unit: str = "milliseconds") -> int:
    try:
        return whole(value, default, bounds, name, unit)
    except Invalid as exc:
        raise ActionError(str(exc)) from None


def _where(x: float, y: float) -> str:
    return f"({round(x)},{round(y)})"


def check_steps(steps: Any) -> list[tuple[str, Any, dict[str, Any]]]:
    """Each step as (kind, what it names, the step), refusing the malformed before anything is played."""
    if not isinstance(steps, list) or not steps:
        raise ActionError(f"steps must be a list of 1 to {MAX_STEPS} steps")
    if len(steps) > MAX_STEPS:
        raise ActionError(
            f"steps must be a list of 1 to {MAX_STEPS} steps ({len(steps)} were sent): split a longer walkthrough into "
            "several sim_act calls"
        )
    checked = []
    for number, step in enumerate(steps, start=1):
        kinds = [kind for kind in STEP_KINDS if isinstance(step, dict) and kind in step]
        if len(kinds) != 1:
            raise ActionError(f'step {number}: a step is one of {", ".join(STEP_KINDS)}, e.g. {{"tap": "e4"}}')
        kind = kinds[0]
        options = STEP_OPTIONS[kind]
        extra = sorted(key for key in step if key != kind and key not in options)
        if extra:
            refused = f"step {number}: {kind} does not take {', '.join(json.dumps(key) for key in extra)}"
            if options:
                refused += f"; beside it: {', '.join(json.dumps(option) for option in options)}"
            if kind == "type":
                refused += ' -- the words go in "type" itself, e.g. {"type": "Groceries", "into": "e1"}'
            raise ActionError(refused)
        for flag in ("clear", "submit"):
            if flag in step and not isinstance(step[flag], bool):
                raise ActionError(f'step {number}: "{flag}" is true or false')
        _check_inner(number, kind, step[kind])
        try:
            _check_values(kind, step[kind], step)
        except ActionError as exc:
            raise ActionError(f"step {number}: {exc}") from None
        checked.append((kind, step[kind], step))
    paused = sum(what for kind, what, _step in checked if kind == "pause")
    if paused > PAUSES_MAX_MS:
        raise ActionError(
            f"the pauses add up to {paused}ms, more than the {PAUSES_MAX_MS} a call may pause; {PAUSE_HINT}"
        )
    return checked


def _check_target(value: Any) -> None:
    """Refuse a target that is neither a ref nor a point; where a point is on the screen is checked as it plays."""
    if isinstance(value, str) or (isinstance(value, list) and len(value) == 2 and all(is_number(v) for v in value)):
        return
    raise ActionError('a target is a ref like "e4" or a point like [201, 319]')


def _check_values(kind: str, what: Any, step: dict[str, Any]) -> None:
    """Refuse what a step names that it could never play, whatever is on the screen: before any step plays."""
    if kind in ("tap", "long_press"):
        _check_target(what)
        if kind == "long_press":
            _whole(step.get("ms"), LONG_PRESS_MS, GESTURE_MS, "ms")
    elif kind == "swipe":
        if not isinstance(what, dict) or "from" not in what:
            raise ActionError('a swipe is {"from": ref or point, "to": point} or {"from", "direction"}')
        _check_target(what["from"])
        if "to" in what:
            _check_target(what["to"])
        elif what.get("direction") not in DIRECTIONS:
            raise ActionError('a swipe needs "to" or a "direction" of up, down, left or right')
        _whole(what.get("ms"), 300, GESTURE_MS, "ms")
    elif kind == "drag":
        path = what.get("path") if isinstance(what, dict) else None
        if not isinstance(path, list) or not 2 <= len(path) <= DRAG_POINTS_MAX:
            raise ActionError(f'a drag is {{"path": [[x, y], ...]}} with 2 to {DRAG_POINTS_MAX} points')
        for point in path:
            _check_target(point)
        _whole(what.get("ms"), 300, GESTURE_MS, "ms")
    elif kind == "type":
        if not isinstance(what, str) or not 0 < len(what) <= TYPE_MAX or "\x00" in what:
            raise ActionError(f"type takes text of 1 to {TYPE_MAX} characters")
        if step.get("into") is not None:
            _check_target(step["into"])
    elif kind == "press":
        if what not in PRESSABLE:
            raise ActionError(f"press takes one of {', '.join(PRESSABLE)}")
    else:
        try:
            whole(what, 0, PAUSE_MS, "pause")
        except Invalid as exc:
            raise ActionError(f"{exc}; {PAUSE_HINT}") from None


def _check_inner(number: int, kind: str, what: Any) -> None:
    """Refuse what a swipe's or a drag's object holds that it does not take."""
    inner = INNER_OPTIONS.get(kind)
    if inner is None or not isinstance(what, dict):
        return
    extra = sorted(key for key in what if key not in inner)
    if not extra:
        return
    refused = f"step {number}: a {kind} does not take {', '.join(json.dumps(key) for key in extra)}"
    refused += f"; it takes {', '.join(json.dumps(option) for option in inner)}"
    if kind == "drag":
        refused += ' -- a drag moves as soon as it touches; {"long_press": T, "ms": 800} holds still'
    raise ActionError(refused)


class AgentActions:
    """Every agent's gestures, snapshots and screenshots, on every device."""

    def __init__(
        self,
        manager: DeviceManager,
        config: ConfigSource,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        extra: ExtraReaders | None = None,
        pixels: OcrReaders | None = None,
        tick: Callable[[float], Awaitable[None]] = asyncio.sleep,
        pause: InputPause | None = None,
        copy: HostCopy | None = None,
    ) -> None:
        self._manager = manager
        self._copy = copy or HostCopy()
        #: Whether a person has paused agents on a device: a host's, asked before every gesture.
        self._pause: InputPause = pause or NoPause()
        self._config = config
        self._clock = clock
        self._sleep = sleep
        #: Paces the working events; apart from `sleep`, which tests make instant, so they would never stop coming.
        self._tick = tick
        self._working = itertools.count(1)
        #: What snapshots read besides the connector's own tree: Xcode's hierarchy, when a scope asks for it.
        self._extra = extra or NoExtraReaders()
        #: What reads a screen's pixels, for the scopes that read them.
        self._pixels = pixels or OcrReaders(NoRecognizer(), supported=False)
        self._memory: dict[tuple[str, str], _Memory] = {}
        #: How many gestures any agent has played on each device, and other changes to it: what a snapshot was read
        #: after (`_Memory.generation`).
        self._gestures: dict[str, int] = {}
        manager.on_end.append(self.forget)

    # -- looking --------------------------------------------------------------------------------------------------

    @staticmethod
    def _ready(instance: DeviceInstance) -> None:
        if instance.session is None or instance.screen is None:
            raise ActionError(f"the simulator is not ready yet (it is {instance.state}); try again in a moment")

    @property
    def pixels(self) -> OcrReaders:
        """What reads a screen's pixels, for the scopes that read them: asked how the text reader stands."""
        return self._pixels

    def capabilities(self, capabilities: Collection[Capability], config: SimConfig) -> frozenset[Capability]:
        """What an agent can do with a device: what its connector can, and reading the screen from its pixels where
        the connector cannot read a tree but can take a screenshot, and the scope reads pixels."""
        found = frozenset(capabilities)
        if Capability.ELEMENT_TREE in found or Capability.SCREENSHOT not in found or not self._pixels.enabled(config):
            return found
        return found | {Capability.ELEMENT_TREE}

    def _readable(self, instance: DeviceInstance, config: SimConfig) -> None:
        """Refuse a device whose screen nothing can read: no tree from its connector, and no pixels read."""
        if instance.session is not None and instance.session.reader is None and not self._pixels.enabled(config):
            raise ActionError(
                "reading the screen needs a connector with an element tree, such as idb or mcpbridge, or "
                f"perception.ocr on; this device is shown through {instance.connector}, which cannot read it. "
                "sim_screenshot still shows the screen"
            )

    def _screen_reader(self, instance: DeviceInstance, config: SimConfig, on_read: OnRead | None = None) -> TreeReader:
        """How this device's screen is read under its scope's settings as they are now; `on_read` is told what each
        reading of its pixels kept."""
        self._readable(instance, config)
        assert instance.session is not None and instance.screen is not None
        session = instance.session
        structured: list[TreeReader] = []
        if session.reader is not None:
            structured.append(DocumentReader(session.reader, instance.connector, screen=instance.screen))
            # What the extra readers merge in -- an app's shared hierarchy, Xcode's -- is read from a simulator's own
            # folders and tools, which a real device has none of.
            if instance.kind == "simulator":
                structured.extend(self._extra.readers(instance.udid, instance.connector, config))
        # Read at two pixels a point of the screen as its last picture showed it: simctl's turns with the device.
        shown = instance.pictured or instance.screen
        pixels = self._pixels.reader(instance.udid, session.screen, shown, config, on_read)
        reader = compose(structured=structured, pixels=pixels, mode=config.ocr_mode, screen=instance.screen)
        assert reader is not None, "a readable screen has a reader"
        return reader

    @staticmethod
    def _sink(instance: DeviceInstance) -> InputSink:
        sink = instance.session.input if instance.session is not None else None
        if sink is None:
            raise ActionError(
                f"touching the device needs a connector that takes input, such as idb; this device is shown through "
                f"{instance.connector}, which cannot touch it"
            )
        return sink

    def _touchable(self, instance: DeviceInstance, caller: Caller) -> None:
        """Refuse a gesture on a real device while the caller's ``real_devices.wda.enabled`` is off -- asked before
        every one, from the settings as they are now, so one turned off mid-batch stops the batch there."""
        if instance.kind == "physical" and not self._config.get(caller.scope).wda_enabled:
            raise ActionError(self._copy.wda_off())

    def forget(self, instance: DeviceInstance) -> None:
        """Let go of every agent's last look at a device that ended: a new session starts from the whole screen."""
        for key in [key for key in self._memory if key[0] == instance.udid]:
            del self._memory[key]
        self._gestures.pop(instance.udid, None)
        self._extra.forget(instance.udid)
        self._pixels.forget(instance.udid)

    async def close(self) -> None:
        """Let go of the readers kept for snapshots."""
        await self._extra.close()
        await self._pixels.close()

    def _remembered(self, instance: DeviceInstance, caller: Caller) -> _Memory:
        return self._memory.setdefault((instance.udid, caller.key), _Memory())

    async def read(self, instance: DeviceInstance, caller: Caller, max_elements: int) -> Snapshot:
        """The screen as it is now, kept as this agent's last look -- for a caller that acts on elements itself."""
        return await self._read(instance, caller, max_elements)

    async def _read(self, instance: DeviceInstance, caller: Caller, max_elements: int) -> Snapshot:
        self._ready(instance)
        config = self._config.get(instance.owner)
        read_from_pixels: list[tuple[RecognizedLine, ...]] = []
        reader = self._screen_reader(instance, config, read_from_pixels.append)
        memory = self._remembered(instance, caller)
        read_at, generation = self._clock(), self._gestures.get(instance.udid, 0)
        try:
            tree = await reader.read()
        except ConnectorError as exc:
            noted = _noted(instance)
            said = f"{exc}; sim_screenshot still shows the screen" + (f"\n{noted}" if noted else "")
            raise ActionError(said) from exc
        instance.orientation, instance.pictured = tree.orientation, tree.screen
        if config.ocr_overlay and read_from_pixels:
            instance.text.show(read_from_pixels[-1])
        else:
            instance.text.hide()
        assert instance.screen is not None
        memory.snapshot = build(
            tree, device=instance.runtime, screen=instance.screen, max_elements=max_elements, previous=memory.snapshot
        )
        memory.stale = memory.unseen = False
        # As the read began, not as it ended: a gesture that landed while it ran may not be in what it read.
        memory.read_at, memory.generation = read_at, generation
        return memory.snapshot

    def _announce(self, instance: DeviceInstance, caller: Caller, gesture: Gesture, lead_ms: int) -> str:
        """Tell every screen watching what the agent is about to do.

        With the scope's ``agent.cursor`` off -- read on every gesture, like every setting -- the screens still hear
        that an agent is using the device, but nothing to draw: no points, no caption, no lead.
        """
        if gesture.kind not in STILL:
            instance.text.hide()
        event_id = f"a{next(self._remembered(instance, caller).ids)}"
        duration_ms = round(gestures.duration(gesture.events) * 1000)
        interface = instance.interface
        assert interface is not None
        # Screens and recordings show the device's own screen, so the interface's points are drawn where they are on it.
        on_device = [interface.on_device(x, y) for x, y in gesture.points]
        if instance.recording is not None and on_device:
            # A recording draws each touch whether or not viewers are shown the agent's cursor.
            instance.recording.agent(gesture.kind, on_device, duration_ms / 1000, lead_ms / 1000)
        agent = self._agent(caller)
        config = self._config.get(instance.owner)
        linger_ms = config.cursor_linger_s * 1000
        if not config.agent_cursor:
            instance.events.publish(
                agent_intent(
                    event_id=event_id,
                    agent=agent,
                    kind=gesture.kind,
                    duration_ms=duration_ms,
                    pointer=False,
                    linger_ms=linger_ms,
                )
            )
            return event_id
        screen = interface.device
        points = [(round(x / screen.width_pt, 4), round(y / screen.height_pt, 4)) for x, y in on_device]
        instance.events.publish(
            agent_intent(
                event_id=event_id,
                agent=agent,
                kind=gesture.kind,
                duration_ms=duration_ms,
                points=points,
                label=gesture.label,
                caption=gesture.caption,
                lead_ms=lead_ms,
                linger_ms=linger_ms,
            )
        )
        return event_id

    @staticmethod
    def _agent(caller: Caller) -> Agent:
        return {"key": caller.key, "title": caller.title}

    def _tell_working(self, caller: Caller, *, ongoing: bool) -> None:
        """Tell the screens of the caller's device, if it has one now, that the agent is at work on a call."""
        instance = self._manager.instance(caller.scope)
        if instance is None:
            return
        linger_ms = self._config.get(instance.owner).cursor_linger_s * 1000
        event_id = f"w{next(self._working)}"
        instance.events.publish(agent_working(event_id, self._agent(caller), linger_ms, ongoing=ongoing))

    @contextlib.asynccontextmanager
    async def working(self, caller: Caller) -> AsyncIterator[None]:
        """For as long as a tool call runs: tell the screens the agent is working when it starts and every
        `WORKING_EVERY_S`, and that the call has ended when it ends -- so the pointer stays through a build, however
        short its linger, and lingers from the end. The device is looked up each time: a call can bring it up or let
        it go."""
        self._tell_working(caller, ongoing=True)

        async def keep() -> None:
            while True:
                await self._tick(WORKING_EVERY_S)
                self._tell_working(caller, ongoing=True)

        task = asyncio.create_task(keep())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            self._tell_working(caller, ongoing=False)

    @staticmethod
    def _done(instance: DeviceInstance, event_id: str, ok: bool) -> None:
        instance.events.publish(agent_done(event_id, ok))

    async def announced(
        self, instance: DeviceInstance, caller: Caller, kind: str, caption: str, work: Awaitable[T]
    ) -> T:
        """Do something viewers should show an agent doing -- launching an app, building -- under a caption."""
        event_id = self._announce(instance, caller, Gesture(kind, kind, caption=caption[:CAPTION_MAX]), 0)
        ok = False
        try:
            result = await work
            ok = True
            return result
        finally:
            self._done(instance, event_id, ok)
            # An app launched or quit, a device changed: what every agent last saw of it may be gone.
            self._unseen(instance, caller, mine=True)

    def _unseen(self, instance: DeviceInstance, caller: Caller, *, mine: bool) -> None:
        """Count a gesture on a device, or another change to it: what every agent last saw of it may have changed out
        of their sight -- this one's too when `mine`. Counted rather than marked on each agent's last look, so a look
        whose read was under way as it landed counts as before it."""
        was = self._gestures.get(instance.udid, 0)
        self._gestures[instance.udid] = was + 1
        memory = self._memory.get((instance.udid, caller.key))
        if not mine and memory is not None and memory.generation == was:
            # Its own step: what follows it reads the screen again anyway, and a ref follows its element (`stale`).
            memory.generation = was + 1

    def _doubtful(self, instance: DeviceInstance, memory: _Memory) -> bool:
        """Whether the screen may have changed out of this agent's sight since its snapshot, so a touch reads it again
        before it trusts a ref: a pause played, something else changed the device -- even while the snapshot was being
        read -- or the snapshot was taken more than `FRESH_S` before this call began -- unless the call allows stale
        refs."""
        if memory.allow_stale or memory.snapshot is None:
            return False
        moved = memory.generation != self._gestures.get(instance.udid, 0)
        return memory.unseen or moved or memory.read_at < memory.began - FRESH_S

    async def snapshot(self, instance: DeviceInstance, caller: Caller, *, mode: str, max_elements: int) -> str:
        """The screen as lines -- or, with ``mode="diff"``, what changed since this agent last looked."""
        self._ready(instance)
        self._readable(instance, self._config.get(instance.owner))
        previous = self._remembered(instance, caller).snapshot
        event_id = self._announce(instance, caller, Gesture("look", "look"), 0)
        ok = False
        try:
            current = await self._read(instance, caller, max_elements)
            ok = True
        finally:
            self._done(instance, event_id, ok)
        said = diff(previous, current) if mode == "diff" and previous is not None else current.text()
        # First, where an agent reading what it can act on cannot miss why it may not be able to.
        noted = _noted(instance)
        return f"{noted}\n{said}" if noted else said

    async def screenshots(
        self,
        instance: DeviceInstance,
        caller: Caller,
        *,
        width: Any,
        region: Any = None,
        frames: Any = 1,
        interval_ms: Any = None,
        max_elements: int = 120,
    ) -> list[Shot]:
        """A JPEG of the screen or a region of it -- or several, `interval_ms` apart, to see an animation."""
        self._ready(instance)
        assert instance.session is not None
        width = _whole(width, 0, SCREENSHOT_WIDTH, "width", "pixels")
        if not isinstance(frames, int) or isinstance(frames, bool) or not 1 <= frames <= FRAMES_MAX:
            raise ActionError(f"frames must be a whole number from 1 to {FRAMES_MAX}")
        interval = _whole(interval_ms, 250, INTERVAL_MS, "interval_ms")
        region_crop = await self._crop(instance, caller, region, max_elements)
        # The region is the interface's, and the picture the device's screen: cut there, then turned upright.
        interface = instance.interface
        assert interface is not None
        crop = None if region_crop is None else interface.crop(region_crop)
        device_width = interface.device_width(width, crop)
        event_id = self._announce(instance, caller, Gesture("look", "look"), 0)
        shots: list[Shot] = []
        try:
            for index in range(frames):
                if index:
                    await self._sleep(interval / 1000)
                shot = await instance.session.screen.screenshot(
                    max_width=device_width, quality=SCREENSHOT_QUALITY, crop=crop
                )
                shots.append(await interface.upright(shot))
        except ConnectorError as exc:
            raise ActionError(str(exc)) from exc
        finally:
            self._done(instance, event_id, len(shots) == frames)
        return shots

    async def _crop(self, instance: DeviceInstance, caller: Caller, region: Any, max_elements: int) -> Crop | None:
        if region is None:
            return None
        if isinstance(region, str):
            x, y, _label, _named = await self._target(instance, caller, region, max_elements, touch=False)
            return Crop(max(0.0, x - REGION_HALF), max(0.0, y - REGION_HALF), REGION_HALF * 2, REGION_HALF * 2)
        if (
            isinstance(region, dict)
            and all(is_number(region.get(key)) for key in ("x", "y", "w", "h"))
            and region["w"] > 0
            and region["h"] > 0
        ):
            return Crop(float(region["x"]), float(region["y"]), float(region["w"]), float(region["h"]))
        raise ActionError('region is a ref like "e4" or {"x", "y", "w", "h"} in points')

    async def region(
        self, instance: DeviceInstance, caller: Caller, region: Any, max_elements: int
    ) -> tuple[float, float, float, float] | None:
        """The part of the device's screen a recording keeps, as `region` names it in the interface: a ref's element
        where its reader said its edges -- else the square a screenshot of it takes -- or a rectangle in points. Answers
        where it begins and how big it is, each a fraction of the device's own screen, as a recording's touches are;
        None for all of it. Refused when none of it is on the screen."""
        self._ready(instance)
        crop = await self._crop(instance, caller, region, max_elements)
        if crop is None:
            return None
        snapshot = self._remembered(instance, caller).snapshot
        element = snapshot.element(region) if isinstance(region, str) and snapshot is not None else None
        if element is not None and element.frame is not None:
            frame = element.frame
            crop = Crop(frame.x, frame.y, frame.width, frame.height)
        interface, screen = instance.interface, instance.screen
        assert interface is not None and screen is not None, "a ready device has a screen"
        on_device = interface.crop(crop)
        left, top = max(0.0, on_device.x / screen.width_pt), max(0.0, on_device.y / screen.height_pt)
        right = min(1.0, (on_device.x + on_device.width) / screen.width_pt)
        bottom = min(1.0, (on_device.y + on_device.height) / screen.height_pt)
        if right <= left or bottom <= top:
            raise ActionError(f"region {region} is off the {screen.width_pt}x{screen.height_pt}pt screen")
        return round(left, 5), round(top, 5), round(right - left, 5), round(bottom - top, 5)

    # -- touching -------------------------------------------------------------------------------------------------

    async def _target(
        self,
        instance: DeviceInstance,
        caller: Caller,
        value: Any,
        max_elements: int,
        *,
        touch: bool = True,
        guard: ObserveGuard | None = None,
    ) -> tuple[float, float, str, str]:
        """Where a step lands, in the interface's points: (x, y, the element's label, how a line names it). A ref a
        touch would begin on is refused when the touch could not land on it (`touch_refusal`), and a touch that acts
        on what observe mode keeps agents off when `guard` says so -- before anything is turned onto the device's
        screen."""
        if isinstance(value, str):
            memory = self._remembered(instance, caller)
            doubted = touch and self._doubtful(instance, memory)
            snapshot = None if memory.stale or doubted else memory.snapshot
            element = snapshot.element(value) if snapshot is not None else None
            if element is None:
                # What this agent saw, unless a step of its own changed the screen since: a ref then goes where it went.
                seen = memory.snapshot if doubted and not memory.stale else None
                snapshot = await self._read(instance, caller, max_elements)
                element = snapshot.element(value)
                if element is not None and seen is not None:
                    self._unchanged(value, seen, snapshot, element)
            if element is None:
                digest = snapshot.digest[:4] if snapshot is not None else ""
                raise ActionError(f"{value} is not on screen now (#{digest}); call sim_snapshot to see what is")
            if touch:
                assert snapshot is not None, "an element is found in a snapshot"
                element = await self._in_front(instance, snapshot, element)
            refused = touch_refusal(value, element) if touch else None
            if refused is not None:
                raise ActionError(refused)
            named = f'{value} "{element.label}"' if element.label else value
            named = f"{named} {_where(element.x, element.y)}"
            if guard is not None:
                assert snapshot is not None, "an element is found in a snapshot"
                named = self._observed(guard, named, [element, *snapshot.at(element.x, element.y)])
            return element.x, element.y, element.label, named
        # A point, as `check_steps` let through.
        interface = instance.interface
        assert interface is not None
        screen = interface.screen
        x, y = float(value[0]), float(value[1])
        if not (0 <= x <= screen.width_pt and 0 <= y <= screen.height_pt):
            raise ActionError(f"{_where(x, y)} is off the {screen.width_pt}x{screen.height_pt}pt screen")
        named = _where(x, y)
        if guard is not None:
            named = self._observed(guard, named, await self._under(instance, caller, value, max_elements))
        return x, y, "", named

    @staticmethod
    def _unchanged(ref: str, seen: Snapshot, now: Snapshot, element: Element) -> None:
        """Refuse a ref whose element is not what this agent saw: another label, or moved more than `MOVED_PT` -- the
        screen changed out of its sight, and what it meant to touch may not be there. Another kind alone is the same
        element read anew, as a ref stays with one that did not move (`perception.snapshot`)."""
        was = seen.element(ref)
        if was is None:
            return
        moved = math.hypot(element.x - was.x, element.y - was.y) > MOVED_PT
        if was.label != element.label or moved:
            raise ActionError(
                f"{ref} changed since #{seen.digest[:4]} → #{now.digest[:4]} (now {element.line()}); call "
                "sim_snapshot to see what is on screen, or pass allow_stale: true to touch it where it is now"
            )

    @staticmethod
    async def _in_front(instance: DeviceInstance, snapshot: Snapshot, element: Element) -> Element:
        """The element as the device says a touch on it lands, where its snapshot cannot tell (`perception.in_front`):
        asked at once, within `in_front.BUDGET_S`. As the snapshot said when the device cannot be asked or does not
        answer."""
        reader = instance.session.points if instance.session is not None else None
        interface = instance.interface
        if reader is None or interface is None:
            return element
        points = in_front.asked(element, snapshot.covers, interface.screen)
        if not points:
            return element
        try:
            answers = await asyncio.wait_for(
                reader.elements_at([interface.on_device(x, y) for x, y in points]), in_front.BUDGET_S
            )
        except (ConnectorError, asyncio.TimeoutError):
            return element
        found = [node_from(answer, instance.connector) if isinstance(answer, dict) else None for answer in answers]
        return in_front.judged(element, points, found)

    async def _looked(self, instance: DeviceInstance, caller: Caller, max_elements: int) -> Snapshot:
        """This agent's last look at the screen, or a new one when a step has played since."""
        memory = self._remembered(instance, caller)
        if memory.snapshot is not None and not memory.stale and not self._doubtful(instance, memory):
            return memory.snapshot
        return await self._read(instance, caller, max_elements)

    async def _under(self, instance: DeviceInstance, caller: Caller, value: Any, max_elements: int) -> list[Element]:
        """What a touch at a target `_target` has found lands on, in this agent's last look: a ref's element first. For
        a point, what the look lists there and then what the device says is there (`_hit`), which the look may not
        list."""
        look = await self._looked(instance, caller, max_elements)
        if isinstance(value, str):
            element = look.element(value)
            assert element is not None, "a ref _target found is in the look it found it in"
            return [element, *look.at(element.x, element.y)]
        x, y = float(value[0]), float(value[1])
        return [*look.at(x, y), *await self._hit(instance, x, y)]

    @staticmethod
    async def _hit(instance: DeviceInstance, x: float, y: float) -> list[Element]:
        """What the device's hit test says is at a point of the interface, asked within `in_front.BUDGET_S`: an element
        a snapshot leaves out -- one that says nothing, or past its ``max_elements``. Nothing when the device cannot be
        asked or does not answer, or answers what only holds the rest (`snapshot.element_of`)."""
        reader = instance.session.points if instance.session is not None else None
        interface = instance.interface
        if reader is None or interface is None:
            return []
        try:
            answers = await asyncio.wait_for(reader.elements_at([interface.on_device(x, y)]), in_front.BUDGET_S)
        except (ConnectorError, asyncio.TimeoutError):
            return []
        nodes = [node_from(answer, instance.connector) for answer in answers if isinstance(answer, dict)]
        return [element for element in (element_of(node, x, y) for node in nodes) if element is not None]

    @staticmethod
    def _observed(guard: ObserveGuard, named: str, under: list[Element]) -> str:
        """How a step's line names a touch observe mode lets land -- confirmed when it had to be -- or why not."""
        return named + AgentActions._kept(guard, named, guard.matched(under), guard.refusal)

    @staticmethod
    def _kept(
        guard: ObserveGuard, named: str, hit: tuple[Element, str] | None, refusal: Callable[[str, Element, str], str]
    ) -> str:
        """What a step's line adds when observe mode found `hit` in what `named` acts on: that it was confirmed, when
        the call says so -- else the step is refused with `refusal`. Nothing, when nothing was found."""
        if hit is None:
            return ""
        if not guard.confirmed:
            raise ActionError(refusal(named, *hit))
        return f', confirmed ("{hit[1]}")'

    async def _submitting(
        self, instance: DeviceInstance, caller: Caller, named: str, into: Any, max_elements: int, guard: ObserveGuard
    ) -> str:
        """What a step's line adds for pressing return in observe mode, typing into `into` when it is not None."""
        field = None
        if into is not None:
            field = next((element for element in await self._under(instance, caller, into, max_elements)
                          if element.kind in ENTRY), None)  # fmt: skip
        look = await self._looked(instance, caller, max_elements)
        return self._kept(guard, named, guard.submitted(look.elements, field), guard.submit_refusal)

    async def _gesture(
        self,
        instance: DeviceInstance,
        caller: Caller,
        kind: str,
        what: Any,
        step: dict[str, Any],
        max_elements: int,
        guard: ObserveGuard | None = None,
    ) -> Gesture:
        if kind in ("tap", "long_press"):
            x, y, label, named = await self._target(instance, caller, what, max_elements, guard=guard)
            if kind == "tap":
                return Gesture("tap", f"tap {named}", tuple(gestures.tap(x, y)), ((x, y),), label)
            hold = _whole(step.get("ms"), LONG_PRESS_MS, GESTURE_MS, "ms")
            return Gesture(
                "long_press", f"long press {named} {hold}ms", tuple(gestures.long_press(x, y, hold / 1000)), ((x, y),),
                label,
            )  # fmt: skip
        if kind == "swipe":
            x, y, label, named = await self._target(instance, caller, what["from"], max_elements)
            if "to" in what:
                end_x, end_y, _label, _named = await self._target(
                    instance, caller, what["to"], max_elements, touch=False
                )
            else:
                # A direction is the interface's: "up" is up the app as it is drawn, however it lies on the screen.
                interface = instance.interface
                assert interface is not None
                screen = interface.screen
                dx, dy = DIRECTIONS[what["direction"]]
                end_x = min(max(x + dx * SWIPE_SHARE * screen.width_pt, 0.0), float(screen.width_pt))
                end_y = min(max(y + dy * SWIPE_SHARE * screen.height_pt, 0.0), float(screen.height_pt))
            kept = ""
            if guard is not None:
                under = await self._under(instance, caller, what["from"], max_elements)
                kept = self._kept(guard, named, guard.swiped(under, (x, y), (end_x, end_y)), guard.swipe_refusal)
            ms = _whole(what.get("ms"), 300, GESTURE_MS, "ms")
            return Gesture(
                "swipe", f"swipe {named} → {_where(end_x, end_y)}{kept}",
                tuple(gestures.swipe((x, y), (end_x, end_y), ms / 1000)), ((x, y), (end_x, end_y)), label,
            )  # fmt: skip
        if kind == "drag":
            path = what["path"]
            points = [
                (await self._target(instance, caller, point, max_elements, touch=index == 0))[:2]
                for index, point in enumerate(path)
            ]
            kept = ""
            if guard is not None:
                # A drag that goes sideways from a row swipes it; one that ends on a trash can drops onto it.
                start = await self._under(instance, caller, path[0], max_elements)
                kept = self._kept(
                    guard, _where(*points[0]), guard.swiped(start, points[0], points[-1]), guard.swipe_refusal
                )
                end = await self._under(instance, caller, path[-1], max_elements)
                kept += self._kept(guard, f"a drag to {_where(*points[-1])}", guard.matched(end), guard.drop_refusal)
            ms = _whole(what.get("ms"), max(300, 16 * len(points)), GESTURE_MS, "ms")
            return Gesture(
                "drag", f"drag {_where(*points[0])} → {_where(*points[-1])} through {len(points)} points{kept}",
                tuple(gestures.drag(points, ms / 1000)), tuple(points),
            )  # fmt: skip
        if kind == "type":
            return await self._typing(instance, caller, what, step, max_elements, guard)
        if kind == "press":
            refused = None if what in gestures.KEYS else button_refusal(what, instance.buttons)
            if refused is not None:
                raise ActionError(refused)
            timed = gestures.key(what) if what in gestures.KEYS else gestures.button(what)
            kept = ""
            if what == "return" and guard is not None:
                kept = await self._submitting(instance, caller, "press return", None, max_elements, guard)
            return Gesture("press", f"press {what}{kept}", tuple(timed), caption=what)
        pause = _whole(what, 0, PAUSE_MS, "pause")
        return Gesture("pause", f"pause {pause}ms", pause_s=pause / 1000)

    async def _typing(
        self,
        instance: DeviceInstance,
        caller: Caller,
        what: Any,
        step: dict[str, Any],
        max_elements: int,
        guard: ObserveGuard | None = None,
    ) -> Gesture:
        events: list[gestures.Timed] = []
        points: tuple[Point, ...] = ()
        summary, start = f'type "{what[:CAPTION_MAX]}"', 0.0
        if step.get("into") is not None:
            x, y, _label, named = await self._target(instance, caller, step["into"], max_elements, guard=guard)
            events += gestures.tap(x, y)
            points, summary, start = ((x, y),), f"{summary} into {named}", FOCUS_S
        submitted = ""
        if step.get("submit") is True and guard is not None:
            submitted = await self._submitting(instance, caller, "submit", step.get("into"), max_elements, guard)
        if step.get("clear") is True:
            events += [(start + at, event) for at, event in gestures.select_all()]
            events += [(start + gestures.TAP_S * 2 + at, event) for at, event in gestures.key("delete")]
            summary, start = f"{summary}, replacing what it held", start + gestures.TAP_S * 4
        if instance.session is not None and instance.session.text is not None:
            # A device that types any text whole needs neither a key for each character nor the pasteboard.
            after = tuple(gestures.key("return")) if step.get("submit") is True else ()
            summary += f" and submit{submitted}" if after else ""
            return Gesture("type", summary, tuple(events), points, caption=what[:CAPTION_MAX], typed=what, after=after)
        entry = await text_entry(what, self._config.get(caller.scope).device_typing, self._manager.keyboard_is_us)
        events += [(start + at, event) for at, event in entry.events]
        start += gestures.duration(entry.events)
        if step.get("submit") is True:
            events += [(start + gestures.TAP_S * 2 + at, event) for at, event in gestures.key("return")]
            summary += f" and submit{submitted}"
        if entry.pasted and paste_refused(instance.runtime):
            # Said, because nothing else would: the paste arrives, iOS drops it, and the field looks untouched.
            summary += (
                f" -- pasted, since {entry.why_pasted}, and {instance.runtime} refuses a paste without asking: check "
                "the field"
            )
        return Gesture("type", summary, tuple(events), points, caption=what[:CAPTION_MAX], text=entry.pasted)

    async def _wait_for_person(self, instance: DeviceInstance) -> None:
        """Wait for a viewer's input to have been still for `QUIET_S` and for any pause to lift, `PERSON_WAIT_S` at
        most. Only a viewer's input is seen here: a finger on a real phone is not, unless the host pauses for it."""
        deadline = self._clock() + PERSON_WAIT_S
        while (touched := self._clock() - instance.person_touch_at < QUIET_S) or self._pause.paused(instance):
            if self._clock() >= deadline:
                raise ActionError(PERSON_USING if touched else PAUSED)
            await self._sleep(PERSON_POLL_S)

    async def _play(self, instance: DeviceInstance, caller: Caller, gesture: Gesture, lead_ms: int) -> None:
        if gesture.kind == "pause":
            await self._sleep(gesture.pause_s)
            return
        self._ready(instance)
        self._touchable(instance, caller)
        sink = self._sink(instance)
        if self._pause.paused(instance):
            raise ActionError(PAUSED)
        event_id = self._announce(instance, caller, gesture, lead_ms)
        ok = False
        try:
            if lead_ms and instance.viewers:
                await self._sleep(lead_ms / 1000)
            if gesture.text:
                await self._manager.control(instance).pbcopy(instance.udid, gesture.text)
            interface = instance.interface
            assert interface is not None
            await sink.hid(interface.events(gestures.play(gesture.events, sleep=self._sleep, clock=self._clock)))
            if gesture.typed and instance.session is not None and instance.session.text is not None:
                if gesture.events:
                    # The field that was tapped takes the keyboard's focus.
                    await self._sleep(FOCUS_S)
                await instance.session.text.type(gesture.typed)
                await sink.hid(interface.events(gestures.play(gesture.after, sleep=self._sleep, clock=self._clock)))
            ok = True
        except (ConnectorError, DeviceControlError) as exc:
            raise ActionError(f"{gesture.summary} failed: {exc}") from exc
        finally:
            self._done(instance, event_id, ok)
            # Every other agent's refs are read again before they touch: this one moved the screen under them.
            self._unseen(instance, caller, mine=False)

    async def act(
        self,
        instance: DeviceInstance,
        caller: Caller,
        steps: Any,
        *,
        wait: Any = None,
        snapshot: str = "diff",
        cursor: bool = True,
        lead_ms: int = 250,
        max_elements: int = 120,
        guard: ObserveGuard | None = None,
        allow_stale: bool = False,
    ) -> str:
        """Play the steps in order, wait if asked, and say what happened and what the screen now shows (`perform`)."""
        acted = await self.perform(
            instance, caller, steps, wait=wait, snapshot=snapshot, cursor=cursor, lead_ms=lead_ms,
            max_elements=max_elements, guard=guard, allow_stale=allow_stale,
        )  # fmt: skip
        return acted.text

    async def perform(
        self,
        instance: DeviceInstance,
        caller: Caller,
        steps: Any,
        *,
        wait: Any = None,
        snapshot: str = "diff",
        cursor: bool = True,
        lead_ms: int = 250,
        max_elements: int = 120,
        guard: ObserveGuard | None = None,
        allow_stale: bool = False,
    ) -> Acted:
        """Play the steps in order, wait if asked, and say what happened and what the screen now shows -- and whether a
        step failed or the wait was not met. A tap, a long press, typing, a sideways swipe, a drag or return on what
        `guard` keeps agents off is refused, unless it is confirmed. `allow_stale` touches refs as this agent's
        snapshot has them, without reading the screen again when it may have changed out of sight."""
        began = self._clock()
        deadline = began + CALL_S
        checked = check_steps(steps)
        if wait is not None:
            # As each step is, a wait that cannot be made is refused before anything plays: taps that went through, then
            # an error, would leave the agent not knowing whether to tap again.
            try:
                parse_wait(wait)
            except Invalid as exc:
                raise ActionError(f"no step was played: the wait is refused: {exc}") from exc
        self._ready(instance)
        self._touchable(instance, caller)
        self._sink(instance)
        if instance.busy:
            raise ActionError(f"the device is busy: {instance.busy}")
        memory = self._remembered(instance, caller)
        lines: list[str] = []
        failed = False
        completed_steps = 0
        failed_step = None
        async with instance.input_lock:
            await self._wait_for_person(instance)
            memory.began, memory.allow_stale = began, allow_stale
            try:
                before = memory.snapshot or await self._read(instance, caller, max_elements)
                for number, (kind, what, step) in enumerate(checked, start=1):
                    try:
                        if self._clock() >= deadline:
                            raise ActionError(
                                f"the call has run {CALL_S:g}s, as long as one may; call again for the rest"
                            )
                        gesture = await self._gesture(instance, caller, kind, what, step, max_elements, guard)
                        await self._play(instance, caller, gesture, lead_ms if cursor else 0)
                    except ActionError as exc:
                        lines.append(f"error step {number}: {exc}")
                        failed = True
                        failed_step = number
                        break
                    if gesture.kind == "pause":
                        # The screen went on without this agent watching it: a ref is checked against it before a touch.
                        memory.unseen = True
                    else:
                        # A ref in a later step is where the screen now has it: a step can open a keyboard over it, or
                        # take it away, and a ref touched where it was would touch whatever is there now.
                        memory.stale = True
                    lines.append(f"ok {gesture.summary} +{round((self._clock() - began) * 1000)}ms")
                    completed_steps += 1
                    read_next = number < len(checked) or (wait is None and snapshot != "none")
                    if gesture.kind in MOVING and read_next:
                        await self._wait(instance, caller, MOVED_SETTLE, max_elements, until=deadline)
            finally:
                memory.began, memory.allow_stale = -math.inf, False
        # The steps above have been played by now: a screen that cannot be read afterwards is said after them, never
        # instead of them -- an agent told only the error would think nothing happened, and repeat a tap. The wait and
        # the read after it only look, so another agent's steps may play meanwhile.
        unmet = False
        if wait is not None and not failed:
            waited = await self._wait(instance, caller, wait, max_elements, until=deadline)
            lines.append(waited.line)
            unmet = not waited.met
        if snapshot != "none" and instance.session is not None:
            try:
                after = await self._read(instance, caller, max_elements)
            except ActionError as exc:
                lines.append(f"the screen after the steps could not be read: {exc}")
            else:
                lines.append(diff(before, after) if snapshot == "diff" else after.text())
        return Acted(
            "\n".join(lines), failed=failed, unmet=unmet, completed_steps=completed_steps, failed_step=failed_step
        )

    async def rotate(
        self, instance: DeviceInstance, caller: Caller, orientation: Orientation, *, max_elements: int = 120
    ) -> Turned:
        """Turn the device so an app that takes `orientation` turns its interface that way, read the screen until the
        app on it lies so -- `TURN_WAIT_S` at most: an app that does not turn that way waits it out -- and once it has
        stopped moving, read it again: how it lies, and what changed since this agent last looked."""
        self._ready(instance)
        assert instance.session is not None
        rotator = instance.session.rotator
        if rotator is None:
            raise ActionError(
                f"turning the device needs the native connector; this device is shown through {instance.connector}, "
                "which cannot turn it"
            )
        if instance.busy:
            raise ActionError(f"the device is busy: {instance.busy}")
        async with instance.input_lock:
            await self._wait_for_person(instance)
            before = self._remembered(instance, caller).snapshot
            try:
                # Where the device's tool can put back how it lay, that is remembered before it is turned.
                await self._manager.changes(instance).turning()
                await self.announced(
                    instance, caller, "device", f"turned to {orientation}", rotator.rotate(orientation)
                )
            except (ConnectorError, DeviceControlError) as exc:
                raise ActionError(str(exc)) from exc
            # Every agent's last look is in the points of the interface as it lay: each reads again before a ref.
            for (udid, _agent), memory in self._memory.items():
                memory.stale = memory.stale or udid == instance.udid
            # Read until the app lies as asked, then wait for it to stop moving: UIKit drops touches while it turns.
            deadline = self._clock() + TURN_WAIT_S
            while not await self._lies(instance, caller, orientation, max_elements) and self._clock() < deadline:
                await self._sleep(TURN_POLL_S)
            await self._wait(instance, caller, TURN_SETTLE, max_elements)
            try:
                after = await self._read(instance, caller, max_elements)
            except ActionError as exc:
                return Turned(None, f"the screen after turning could not be read: {exc}")
        return Turned(instance.orientation, diff(before, after) if before is not None else after.text())

    async def _lies(
        self, instance: DeviceInstance, caller: Caller, orientation: Orientation, max_elements: int
    ) -> bool:
        """Whether the app on screen is read lying this way now; a screen that cannot be read yet is not."""
        try:
            await self._read(instance, caller, max_elements)
        except ActionError:
            return False
        return instance.orientation == orientation

    async def _wait(
        self, instance: DeviceInstance, caller: Caller, spec: Any, max_elements: int, *, until: float = math.inf
    ) -> Waited:
        """Wait as `spec` asks, giving up by `until` whatever its own timeout."""
        try:
            wait = parse_wait(spec)
        except Invalid as exc:
            return Waited(f"wait skipped: {exc}", False)
        assert instance.session is not None and instance.screen is not None
        wait = replace(wait, timeout_s=max(0.0, min(wait.timeout_s, until - self._clock())))

        async def read() -> Snapshot:
            return await self._read(instance, caller, max_elements)

        settle = ScreenshotSettle(
            read=read,
            source=instance.session.screen,
            screen=instance.screen,
            clock=self._clock,
            sleep=self._sleep,
            unreadable=(ActionError,),
            stillness=stillness_for(self._config.get(instance.owner)),
            orientation=lambda: instance.orientation,
        )
        waiter = Waiter(read=read, settle=settle, clock=self._clock, sleep=self._sleep, unreadable=(ActionError,))
        return await waiter.run(wait)
