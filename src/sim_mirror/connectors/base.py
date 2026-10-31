# SPDX-License-Identifier: Apache-2.0
"""What a connector is: something that reaches a device, says what it can do there, and hands over the parts to drive.

A connector probes its environment (`Connector.probe`): whether it can be used on this Mac with these settings, which
`Capability`s it has, the versions it found, and why not when it cannot. SimMirror chooses one
(`registry.ConnectorRegistry.select`) and attaches it to a booted device (`Connector.attach`), getting a
`DeviceSession` with the roles the core drives -- each present only when the connector has the capabilities behind it:

* `ScreenSource` -- the screen's size, screenshots, and with ``STREAM_H264`` an H.264 stream;
* `InputSink` -- touches, buttons and keys, one stream per gesture (the ``INPUT_*`` capabilities);
* `ScreenReader` -- what is on screen, as an accessibility document (``ELEMENT_TREE``);
* `Rotator` -- turning a simulator, as Simulator.app's Device > Rotate does (``ROTATE``).

Booting, installing and launching are the device's own tool's whichever connector is in use -- simctl for a simulator,
devicectl for a real device, through `sim_mirror.core.backends` -- so they are not roles here. A connector's report says
which kinds of device it drives (`ConnectorReport.kinds`); simulators, unless it says otherwise.

Coordinates are the device's **points**, in portrait: what a touch takes and what a screenshot shows, whichever way an
app is turned on the screen -- a simulator's framebuffer and digitizer do not turn. A screenshot's size is in pixels.
`Screen` holds both and the scale between them. An accessibility document is in the points of the interface it reads,
and says in its ``orientation`` which way that is turned on the screen, when it is (`perception.orientation`).
"""

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from sim_mirror.config.model import SimConfig
    from sim_mirror.perception.orientation import Orientation
    from sim_mirror.protocol import DeviceKind, PanelButton


class Capability(str, Enum):
    """Something a connector can do with a device. The values are the protocol's (`protocol/v1`)."""

    LIFECYCLE = "lifecycle"
    DEVICE_LIST = "device_list"
    APPEARANCE = "appearance"
    OPEN_URL = "open_url"
    APP_INSTALL = "app_install"
    APP_LAUNCH = "app_launch"
    LOGS = "logs"
    SCREENSHOT = "screenshot"
    STREAM_JPEG = "stream_jpeg"
    STREAM_H264 = "stream_h264"
    INPUT_TOUCH = "input_touch"
    INPUT_BUTTON = "input_button"
    INPUT_KEY = "input_key"
    INPUT_TEXT = "input_text"
    ELEMENT_TREE = "element_tree"
    BUILD_PREVIEW = "build_preview"
    RECORD = "record"
    STATUS_BAR = "status_bar"
    LOCATION = "location"
    ACCESSIBILITY = "accessibility"
    ROTATE = "rotate"


#: The capabilities that need an `InputSink`.
INPUT_CAPABILITIES = frozenset(
    {Capability.INPUT_TOUCH, Capability.INPUT_BUTTON, Capability.INPUT_KEY, Capability.INPUT_TEXT}
)

Phase = Literal["down", "up"]

#: The hardware buttons a device has: an iPhone's, then an Android device's.
BUTTONS = ("home", "lock", "side", "siri", "apple_pay", "back", "app_switch", "volume_up", "volume_down")
#: The buttons a session with ``INPUT_BUTTON`` presses unless it says otherwise: a simulator's, as the viewer offers.
IOS_BUTTONS: tuple[PanelButton, ...] = ("home", "lock", "side", "siri")


@dataclass(frozen=True)
class Screen:
    width_px: int
    height_px: int
    width_pt: int
    height_pt: int
    scale: float


@dataclass(frozen=True)
class Shot:
    """A JPEG of the screen and its size in pixels."""

    jpeg: bytes
    width: int
    height: int


@dataclass(frozen=True)
class Crop:
    """A region of the screen, in points."""

    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class HidEvent:
    """One input event: a finger, a button or a key going down or up."""

    kind: Literal["touch", "button", "key"]
    phase: Phase
    x: float = 0.0
    y: float = 0.0
    button: str = ""
    code: int = 0

    @classmethod
    def touch(cls, phase: Phase, x: float, y: float) -> HidEvent:
        return cls("touch", phase, x=x, y=y)

    @classmethod
    def press(cls, button: str, phase: Phase) -> HidEvent:
        if button not in BUTTONS:
            raise ValueError(f"not a button: {button!r}")
        return cls("button", phase, button=button)

    @classmethod
    def key(cls, code: int, phase: Phase) -> HidEvent:
        return cls("key", phase, code=code)


class ConnectorError(Exception):
    """A call on a device that did not work, said so a person can act on it."""


def button_refusal(button: str, pressable: Sequence[str]) -> str | None:
    """Why `button` cannot be pressed on a device with `pressable`; None when it can."""
    if button in pressable:
        return None
    if not pressable:
        return f"the {button} button cannot be pressed on this device: it takes no button presses"
    return f"this device has no {button} button to press; it has {', '.join(pressable)}"


class ConnectorUnavailable(ConnectorError):
    """A connector that cannot reach a device, with the HTTP status that means."""

    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


class RefusedStream:
    """An async iterator whose first step refuses: a stream a screen cannot give, saying what would give it."""

    def __init__(self, message: str) -> None:
        self._message = message

    def __aiter__(self) -> RefusedStream:
        return self

    async def __anext__(self) -> bytes:
        raise ConnectorError(self._message)


class ScreenSource(Protocol):
    async def describe(self) -> Screen:
        """The screen's size, in pixels and in points."""
        ...

    async def screenshot(self, *, max_width: int, quality: int, crop: Crop | None = None) -> Shot:
        """A JPEG of the screen, or of a region of it, no wider than `max_width` pixels."""
        ...

    def h264(self, *, fps: int, scale: float, key_frame_s: float, bitrate: int) -> AsyncIterator[bytes]:
        """The screen as an Annex-B H.264 stream, until the iterator is closed."""
        ...


class InputSink(Protocol):
    async def hid(self, events: AsyncIterable[HidEvent]) -> None:
        """Send events down one stream, in order, as they come: a drag is one stream."""
        ...


class TextSink(Protocol):
    async def type(self, text: str) -> None:
        """Type text of any kind into what has the keyboard's focus, whole: a device that can needs no pasteboard."""
        ...


class ScreenReader(Protocol):
    async def accessibility(self) -> dict[str, Any]:
        """What is on screen: the consolidated accessibility document, interactable elements only."""
        ...


class Rotator(Protocol):
    async def rotate(self, orientation: Orientation) -> None:
        """Turn the device, as Simulator.app's Device > Rotate does, so an app that takes `orientation` turns its
        interface that way. The name is the interface's, as an accessibility document says it (``ROTATE``)."""
        ...


class PointReader(Protocol):
    async def elements_at(self, points: Sequence[tuple[float, float]]) -> list[dict[str, Any] | None]:
        """What accessibility hit-tests at each point of the screen, asked at once: the element found there, as an
        element of the accessibility document is written but with no children, or None where nothing is or it would
        not say. The points are the screen's own portrait ones; the elements are in the interface's points."""
        ...


#: The kinds of device a connector drives unless its report says otherwise.
SIMULATORS: frozenset[DeviceKind] = frozenset({"simulator"})


@dataclass(frozen=True)
class ConnectorReport:
    """What a connector found when it looked at this Mac."""

    name: str
    available: bool
    capabilities: frozenset[Capability] = frozenset()
    versions: Mapping[str, str] = field(default_factory=dict)
    #: Why it cannot be used; empty when it can.
    reasons: tuple[str, ...] = ()
    #: The kinds of device it drives: simulators, real devices, or both.
    kinds: frozenset[DeviceKind] = SIMULATORS

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "available": self.available,
            "capabilities": sorted(capability.value for capability in self.capabilities),
            "versions": dict(self.versions),
            "reasons": list(self.reasons),
            "kinds": sorted(self.kinds),
        }


def _always() -> bool:
    return True


@dataclass(eq=False)
class DeviceSession:
    """A connector attached to one booted device: the roles to drive it by, and how to let it go."""

    connector: str
    capabilities: frozenset[Capability]
    screen: ScreenSource
    input: InputSink | None = None
    reader: ScreenReader | None = None
    #: What types text whole, when the connector can; None where text is typed as keys or pasted.
    text: TextSink | None = None
    #: What turns the device, where the connector has ``ROTATE``.
    rotator: Rotator | None = None
    #: The most frames a second this connector can stream the screen at; None when there is no limit of its own.
    fps_limit: int | None = None
    #: Whether what the session relies on -- a helper process -- is still running.
    is_alive: Callable[[], bool] = _always
    on_close: Callable[[], Awaitable[None]] | None = None
    #: What a person should know about what this session can do, and what would let it do more; None when nothing.
    note: str | None = None
    #: What says which element is at a point of the screen, where the connector can; None where it cannot.
    points: PointReader | None = None
    #: The buttons this session presses, where it has ``INPUT_BUTTON``; None for a simulator's (`IOS_BUTTONS`).
    buttons: tuple[PanelButton, ...] | None = None
    #: Whether the device could now be reached better than this session reaches it -- a real device's cable plugged
    #: back in -- asked again and again while it runs, and free to wait a while for it. When so, a second session is
    #: attached beside this one and replaces it once it is up, so a session that says this holds nothing the second
    #: would need. None where nothing would be better.
    better: Callable[[], Awaitable[bool]] | None = None
    #: Asks what drives the device whether it answers now: None when it does, else why not -- for a status that is to
    #: say what is so rather than what was set up. None where nothing is asked.
    answers: Callable[[], Awaitable[str | None]] | None = None
    #: What a helper the session runs -- a cabled device's screen reader -- last said went wrong since it started, read
    #: when asked: None while it has said nothing. None where nothing is asked.
    last_error: Callable[[], str | None] | None = None
    #: Which app is in front -- its bundle id -- asked of what drives the device: None when it cannot say now. None
    #: where nothing can be asked.
    front_app: Callable[[], Awaitable[str | None]] | None = None
    _closed: bool = field(default=False, init=False)

    @property
    def alive(self) -> bool:
        return not self._closed and self.is_alive()

    def can(self, capability: Capability) -> bool:
        return capability in self.capabilities

    @property
    def pressable(self) -> tuple[PanelButton, ...]:
        """The buttons that can be pressed through this session: none without ``INPUT_BUTTON``."""
        if not self.can(Capability.INPUT_BUTTON):
            return ()
        return IOS_BUTTONS if self.buttons is None else self.buttons

    async def close(self) -> None:
        """Let go of the device. The device itself keeps running. Closing twice is closing once."""
        if self._closed:
            return
        self._closed = True
        if self.on_close is not None:
            await self.on_close()


@runtime_checkable
class ReportsTrouble(Protocol):
    """A connector that remembers, for each device, what last kept what drives it from driving it -- WebDriverAgent on
    a real iPhone -- and what it is doing about it now. Asked for a refusal, a health check and a status."""

    def last_error(self, udid: str) -> str | None:
        """The last thing that kept the device from being driven, with when; None when nothing has since it was."""
        ...

    def progress(self, udid: str) -> str | None:
        """What is being done now that keeps the device from being driven yet -- else its last error, or None."""
        ...


@runtime_checkable
class ReportsStarting(Protocol):
    """A connector that starts what drives a device -- WebDriverAgent on a real iPhone -- within a time it is given,
    and can say how much of that time is left: a tool call waits that long for it rather than refusing early."""

    def starting_left(self, udid: str) -> float | None:
        """Seconds left of the time what drives the device is given to start; None when nothing is starting."""
        ...


class Connector(Protocol):
    @property
    def name(self) -> str:
        """How settings, reports and the viewer name it: ``idb``, ``simctl``."""
        ...

    async def probe(self, config: SimConfig) -> ConnectorReport:
        """Whether it can be used here with these settings, what it can do, and why not. Cheap enough to call often."""
        ...

    async def attach(self, udid: str, config: SimConfig) -> DeviceSession:
        """Reach a booted device. Raises `ConnectorUnavailable` with a reason when it cannot."""
        ...

    async def reap_orphans(self) -> int:
        """End whatever a previous run of this process's host left behind for this connector. Answers how many."""
        ...
