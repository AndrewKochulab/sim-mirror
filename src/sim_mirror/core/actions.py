# SPDX-License-Identifier: Apache-2.0
"""What an agent does to a device: steps played in order, and drawn on every screen watching it.

The agent tools come here for everything that looks at or touches a device. A call is a batch of steps -- tap, long
press, swipe, drag, type, press, pause -- because a round trip is what an agent pays for, not a gesture. After the
steps it can wait, for text to appear or go or for the screen to settle after an animation (`perception.wait`), and it
answers with what changed on screen (`perception.snapshot`).

**Every gesture is announced before it lands.** The device's `EventBus` carries an agent intent with the gesture's
points as shares of the screen, so a viewer's pointer glides there; while anyone is watching, the gesture then waits
``agent.cursor_lead_ms`` for the pointer to arrive. Nobody watching, nothing waits.

**A person comes first.** Agents take turns (`instance.input_lock`) and wait for a person's hand to have been still for
`QUIET_S`, giving up after `PERSON_WAIT_S` with a reason. A person never waits for an agent. While tests run on the
device (`instance.busy`) no agent gesture is played at all.

**Refs are read against the screen as it is.** Each step looks its ref up in the snapshot this agent last took, and
reads the screen again when the ref is not in it. A ref whose element is gone is an error that says to look again --
never a tap on whatever is there now. A step that cannot be played ends the batch; the steps before it stay done, and
the answer says which.

**What the connector cannot do is said plainly.** A device mirrored by a connector that cannot read or touch the screen
refuses snapshots and steps with which connector could.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from sim_mirror.connectors.base import ConnectorError, Crop, InputSink, ScreenReader, Shot
from sim_mirror.core import gestures
from sim_mirror.core.instance import DeviceInstance
from sim_mirror.core.manager import DeviceManager
from sim_mirror.core.text_entry import paste_refused, text_entry
from sim_mirror.perception.readers import DocumentReader, ExtraReaders, MergedReader, NoExtraReaders
from sim_mirror.perception.settle import ScreenshotSettle
from sim_mirror.perception.snapshot import Snapshot, build, diff
from sim_mirror.perception.wait import Waiter, parse_wait
from sim_mirror.platform.simctl import SimctlError
from sim_mirror.protocol import Agent, agent_done, agent_intent
from sim_mirror.seams import Caller, ConfigSource
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
MAX_STEPS = 20
TYPE_MAX = 2000
CAPTION_MAX = 80
DRAG_POINTS_MAX = 50
GESTURE_MS = (50, 5000)
LONG_PRESS_MS = 800
PAUSE_MS = (0, 3000)
#: How far a swipe given only a direction goes, as a share of the screen.
SWIPE_SHARE = 0.35
#: How long after tapping a field the text goes into it, so the field has focus.
FOCUS_S = 0.35
QUIET_S = 0.3
PERSON_WAIT_S = 2.0
PERSON_POLL_S = 0.05
PRESSABLE = (*gestures.KEYS, "home", "lock", "side", "siri")
DIRECTIONS = {"up": (0.0, -1.0), "down": (0.0, 1.0), "left": (-1.0, 0.0), "right": (1.0, 0.0)}
SCREENSHOT_WIDTH = (160, 1200)
SCREENSHOT_QUALITY = 70
FRAMES_MAX = 6
INTERVAL_MS = (50, 2000)
#: How far around an element a screenshot of it reaches, in points.
REGION_HALF = 60.0

Point = tuple[float, float]


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


@dataclass
class _Memory:
    snapshot: Snapshot | None = None
    ids: itertools.count[int] = field(default_factory=lambda: itertools.count(1))


def _whole(value: Any, default: int, bounds: tuple[int, int], name: str, unit: str = "milliseconds") -> int:
    try:
        return whole(value, default, bounds, name, unit)
    except Invalid as exc:
        raise ActionError(str(exc)) from None


def _where(x: float, y: float) -> str:
    return f"({round(x)},{round(y)})"


def check_steps(steps: Any) -> list[tuple[str, Any, dict[str, Any]]]:
    """Each step as (kind, what it names, the step), refusing the malformed before anything is played."""
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise ActionError(f"steps must be a list of 1 to {MAX_STEPS} steps")
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
        checked.append((kind, step[kind], step))
    return checked


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
    ) -> None:
        self._manager = manager
        self._config = config
        self._clock = clock
        self._sleep = sleep
        #: What snapshots read besides the connector's own tree: Xcode's hierarchy, when a scope asks for it.
        self._extra = extra or NoExtraReaders()
        self._memory: dict[tuple[str, str], _Memory] = {}
        manager.on_end.append(self.forget)

    # -- looking --------------------------------------------------------------------------------------------------

    @staticmethod
    def _ready(instance: DeviceInstance) -> None:
        if instance.session is None or instance.screen is None:
            raise ActionError(f"the simulator is not ready yet (it is {instance.state}); try again in a moment")

    @staticmethod
    def _reader(instance: DeviceInstance) -> ScreenReader:
        reader = instance.session.reader if instance.session is not None else None
        if reader is None:
            raise ActionError(
                "reading the screen needs a connector with an element tree, such as idb or mcpbridge; this device is "
                f"shown through {instance.connector}, which cannot read it. sim_screenshot still shows the screen"
            )
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

    def forget(self, instance: DeviceInstance) -> None:
        """Let go of every agent's last look at a device that ended: a new session starts from the whole screen."""
        for key in [key for key in self._memory if key[0] == instance.udid]:
            del self._memory[key]
        self._extra.forget(instance.udid)

    async def close(self) -> None:
        """Let go of the readers kept for snapshots."""
        await self._extra.close()

    def _remembered(self, instance: DeviceInstance, caller: Caller) -> _Memory:
        return self._memory.setdefault((instance.udid, caller.key), _Memory())

    async def read(self, instance: DeviceInstance, caller: Caller, max_elements: int) -> Snapshot:
        """The screen as it is now, kept as this agent's last look -- for a caller that acts on elements itself."""
        return await self._read(instance, caller, max_elements)

    async def _read(self, instance: DeviceInstance, caller: Caller, max_elements: int) -> Snapshot:
        self._ready(instance)
        reader = self._reader(instance)
        memory = self._remembered(instance, caller)
        extra = self._extra.readers(instance.udid, instance.connector, self._config.get(instance.owner))
        try:
            tree = await MergedReader(DocumentReader(reader, instance.connector), extra).read()
        except ConnectorError as exc:
            raise ActionError(f"{exc}; sim_screenshot still shows the screen") from exc
        assert instance.screen is not None
        memory.snapshot = build(
            tree, device=instance.runtime, screen=instance.screen, max_elements=max_elements, previous=memory.snapshot
        )
        return memory.snapshot

    def _announce(self, instance: DeviceInstance, caller: Caller, gesture: Gesture, lead_ms: int) -> str:
        """Tell every screen watching what the agent is about to do.

        With the scope's ``agent.cursor`` off -- read on every gesture, like every setting -- the screens still hear
        that an agent is using the device, but nothing to draw: no points, no caption, no lead.
        """
        event_id = f"a{next(self._remembered(instance, caller).ids)}"
        duration_ms = round(gestures.duration(gesture.events) * 1000)
        agent: Agent = {"key": caller.key, "title": caller.title}
        if not self._config.get(instance.owner).agent_cursor:
            instance.events.publish(
                agent_intent(event_id=event_id, agent=agent, kind=gesture.kind, duration_ms=duration_ms, pointer=False)
            )
            return event_id
        screen = instance.screen
        assert screen is not None
        points = [(round(x / screen.width_pt, 4), round(y / screen.height_pt, 4)) for x, y in gesture.points]
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
            )
        )
        return event_id

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

    async def snapshot(self, instance: DeviceInstance, caller: Caller, *, mode: str, max_elements: int) -> str:
        """The screen as lines -- or, with ``mode="diff"``, what changed since this agent last looked."""
        self._ready(instance)
        self._reader(instance)
        previous = self._remembered(instance, caller).snapshot
        event_id = self._announce(instance, caller, Gesture("look", "look"), 0)
        ok = False
        try:
            current = await self._read(instance, caller, max_elements)
            ok = True
        finally:
            self._done(instance, event_id, ok)
        return diff(previous, current) if mode == "diff" and previous is not None else current.text()

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
        crop = await self._crop(instance, caller, region, max_elements)
        event_id = self._announce(instance, caller, Gesture("look", "look"), 0)
        shots: list[Shot] = []
        try:
            for index in range(frames):
                if index:
                    await self._sleep(interval / 1000)
                shots.append(
                    await instance.session.screen.screenshot(max_width=width, quality=SCREENSHOT_QUALITY, crop=crop)
                )
        except ConnectorError as exc:
            raise ActionError(str(exc)) from exc
        finally:
            self._done(instance, event_id, len(shots) == frames)
        return shots

    async def _crop(self, instance: DeviceInstance, caller: Caller, region: Any, max_elements: int) -> Crop | None:
        if region is None:
            return None
        if isinstance(region, str):
            x, y, _label, _named = await self._target(instance, caller, region, max_elements)
            return Crop(max(0.0, x - REGION_HALF), max(0.0, y - REGION_HALF), REGION_HALF * 2, REGION_HALF * 2)
        if (
            isinstance(region, dict)
            and all(is_number(region.get(key)) for key in ("x", "y", "w", "h"))
            and region["w"] > 0
            and region["h"] > 0
        ):
            return Crop(float(region["x"]), float(region["y"]), float(region["w"]), float(region["h"]))
        raise ActionError('region is a ref like "e4" or {"x", "y", "w", "h"} in points')

    # -- touching -------------------------------------------------------------------------------------------------

    async def _target(
        self, instance: DeviceInstance, caller: Caller, value: Any, max_elements: int
    ) -> tuple[float, float, str, str]:
        """Where a step lands: (x, y, the element's label, how a line names it)."""
        screen = instance.screen
        assert screen is not None
        if isinstance(value, str):
            snapshot = self._remembered(instance, caller).snapshot
            element = snapshot.element(value) if snapshot is not None else None
            if element is None:
                snapshot = await self._read(instance, caller, max_elements)
                element = snapshot.element(value)
            if element is None:
                digest = snapshot.digest[:4] if snapshot is not None else ""
                raise ActionError(f"{value} is not on screen now (#{digest}); call sim_snapshot to see what is")
            named = f'{value} "{element.label}"' if element.label else value
            return element.x, element.y, element.label, f"{named} {_where(element.x, element.y)}"
        if isinstance(value, list) and len(value) == 2 and all(is_number(v) for v in value):
            x, y = float(value[0]), float(value[1])
            if not (0 <= x <= screen.width_pt and 0 <= y <= screen.height_pt):
                raise ActionError(f"{_where(x, y)} is off the {screen.width_pt}x{screen.height_pt}pt screen")
            return x, y, "", _where(x, y)
        raise ActionError('a target is a ref like "e4" or a point like [201, 319]')

    async def _gesture(
        self, instance: DeviceInstance, caller: Caller, kind: str, what: Any, step: dict[str, Any], max_elements: int
    ) -> Gesture:
        screen = instance.screen
        assert screen is not None
        if kind in ("tap", "long_press"):
            x, y, label, named = await self._target(instance, caller, what, max_elements)
            if kind == "tap":
                return Gesture("tap", f"tap {named}", tuple(gestures.tap(x, y)), ((x, y),), label)
            hold = _whole(step.get("ms"), LONG_PRESS_MS, GESTURE_MS, "ms")
            return Gesture(
                "long_press", f"long press {named} {hold}ms", tuple(gestures.long_press(x, y, hold / 1000)), ((x, y),),
                label,
            )  # fmt: skip
        if kind == "swipe":
            if not isinstance(what, dict) or "from" not in what:
                raise ActionError('a swipe is {"from": ref or point, "to": point} or {"from", "direction"}')
            x, y, label, named = await self._target(instance, caller, what["from"], max_elements)
            if "to" in what:
                end_x, end_y, _label, _named = await self._target(instance, caller, what["to"], max_elements)
            elif what.get("direction") in DIRECTIONS:
                dx, dy = DIRECTIONS[what["direction"]]
                end_x = min(max(x + dx * SWIPE_SHARE * screen.width_pt, 0.0), float(screen.width_pt))
                end_y = min(max(y + dy * SWIPE_SHARE * screen.height_pt, 0.0), float(screen.height_pt))
            else:
                raise ActionError('a swipe needs "to" or a "direction" of up, down, left or right')
            ms = _whole(what.get("ms"), 300, GESTURE_MS, "ms")
            return Gesture(
                "swipe", f"swipe {named} → {_where(end_x, end_y)}",
                tuple(gestures.swipe((x, y), (end_x, end_y), ms / 1000)), ((x, y), (end_x, end_y)), label,
            )  # fmt: skip
        if kind == "drag":
            path = what.get("path") if isinstance(what, dict) else None
            if not isinstance(path, list) or not 2 <= len(path) <= DRAG_POINTS_MAX:
                raise ActionError(f'a drag is {{"path": [[x, y], ...]}} with 2 to {DRAG_POINTS_MAX} points')
            points = [(await self._target(instance, caller, point, max_elements))[:2] for point in path]
            ms = _whole(what.get("ms"), max(300, 16 * len(points)), GESTURE_MS, "ms")
            return Gesture(
                "drag", f"drag {_where(*points[0])} → {_where(*points[-1])} through {len(points)} points",
                tuple(gestures.drag(points, ms / 1000)), tuple(points),
            )  # fmt: skip
        if kind == "type":
            return await self._typing(instance, caller, what, step, max_elements)
        if kind == "press":
            if what not in PRESSABLE:
                raise ActionError(f"press takes one of {', '.join(PRESSABLE)}")
            timed = gestures.key(what) if what in gestures.KEYS else gestures.button(what)
            return Gesture("press", f"press {what}", tuple(timed), caption=what)
        pause = _whole(what, 0, PAUSE_MS, "pause")
        return Gesture("pause", f"pause {pause}ms", pause_s=pause / 1000)

    async def _typing(
        self, instance: DeviceInstance, caller: Caller, what: Any, step: dict[str, Any], max_elements: int
    ) -> Gesture:
        if not isinstance(what, str) or not 0 < len(what) <= TYPE_MAX or "\x00" in what:
            raise ActionError(f"type takes text of 1 to {TYPE_MAX} characters")
        events: list[gestures.Timed] = []
        points: tuple[Point, ...] = ()
        summary, start = f'type "{what[:CAPTION_MAX]}"', 0.0
        if step.get("into") is not None:
            x, y, _label, named = await self._target(instance, caller, step["into"], max_elements)
            events += gestures.tap(x, y)
            points, summary, start = ((x, y),), f"{summary} into {named}", FOCUS_S
        if step.get("clear") is True:
            events += [(start + at, event) for at, event in gestures.select_all()]
            events += [(start + gestures.TAP_S * 2 + at, event) for at, event in gestures.key("delete")]
            summary, start = f"{summary}, replacing what it held", start + gestures.TAP_S * 4
        entry = await text_entry(what, self._config.get(caller.scope).device_typing, self._manager.keyboard_is_us)
        events += [(start + at, event) for at, event in entry.events]
        start += gestures.duration(entry.events)
        if step.get("submit") is True:
            events += [(start + gestures.TAP_S * 2 + at, event) for at, event in gestures.key("return")]
            summary += " and submit"
        if entry.pasted and paste_refused(instance.runtime):
            # Said, because nothing else would: the paste arrives, iOS drops it, and the field looks untouched.
            summary += (
                f" -- pasted, since {entry.why_pasted}, and {instance.runtime} refuses a paste without asking: check "
                "the field"
            )
        return Gesture("type", summary, tuple(events), points, caption=what[:CAPTION_MAX], text=entry.pasted)

    async def _wait_for_person(self, instance: DeviceInstance) -> None:
        deadline = self._clock() + PERSON_WAIT_S
        while self._clock() - instance.person_touch_at < QUIET_S:
            if self._clock() >= deadline:
                raise ActionError("the person watching is using the device; try again in a moment")
            await self._sleep(PERSON_POLL_S)

    async def _play(self, instance: DeviceInstance, caller: Caller, gesture: Gesture, lead_ms: int) -> None:
        if gesture.kind == "pause":
            await self._sleep(gesture.pause_s)
            return
        self._ready(instance)
        sink = self._sink(instance)
        event_id = self._announce(instance, caller, gesture, lead_ms)
        ok = False
        try:
            if lead_ms and instance.viewers:
                await self._sleep(lead_ms / 1000)
            if gesture.text:
                await self._manager.simctl(instance).pbcopy(instance.udid, gesture.text)
            await sink.hid(gestures.play(gesture.events, sleep=self._sleep, clock=self._clock))
            ok = True
        except (ConnectorError, SimctlError) as exc:
            raise ActionError(f"{gesture.summary} failed: {exc}") from exc
        finally:
            self._done(instance, event_id, ok)

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
    ) -> str:
        """Play the steps in order, wait if asked, and say what happened and what the screen now shows."""
        checked = check_steps(steps)
        self._ready(instance)
        self._sink(instance)
        if instance.busy:
            raise ActionError(f"the device is busy: {instance.busy}")
        async with instance.input_lock:
            await self._wait_for_person(instance)
            before = self._remembered(instance, caller).snapshot or await self._read(instance, caller, max_elements)
            lines: list[str] = []
            failed = False
            for number, (kind, what, step) in enumerate(checked, start=1):
                try:
                    gesture = await self._gesture(instance, caller, kind, what, step, max_elements)
                    await self._play(instance, caller, gesture, lead_ms if cursor else 0)
                except ActionError as exc:
                    lines.append(f"error step {number}: {exc}")
                    failed = True
                    break
                lines.append(f"ok {gesture.summary}")
            # The steps above have been played by now: a screen that cannot be read afterwards is said after them,
            # never instead of them -- an agent told only the error would think nothing happened, and repeat a tap.
            if wait is not None and not failed:
                try:
                    lines.append(await self._wait(instance, caller, wait, max_elements))
                except ActionError as exc:
                    lines.append(f"wait stopped: {exc}")
            if snapshot != "none" and instance.session is not None:
                try:
                    after = await self._read(instance, caller, max_elements)
                except ActionError as exc:
                    lines.append(f"the screen after the steps could not be read: {exc}")
                else:
                    lines.append(diff(before, after) if snapshot == "diff" else after.text())
        return "\n".join(lines)

    async def _wait(self, instance: DeviceInstance, caller: Caller, spec: Any, max_elements: int) -> str:
        try:
            wait = parse_wait(spec)
        except Invalid as exc:
            return f"wait skipped: {exc}"
        assert instance.session is not None and instance.screen is not None

        async def read() -> Snapshot:
            return await self._read(instance, caller, max_elements)

        settle = ScreenshotSettle(
            read=read,
            source=instance.session.screen,
            screen=instance.screen,
            clock=self._clock,
            sleep=self._sleep,
            unreadable=(ActionError,),
        )
        return await Waiter(read=read, settle=settle, clock=self._clock, sleep=self._sleep).run(wait)
