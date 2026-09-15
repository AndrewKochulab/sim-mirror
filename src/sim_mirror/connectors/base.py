# SPDX-License-Identifier: Apache-2.0
"""What a connector is: something that reaches a device, says what it can do there, and hands over the parts to drive.

A connector probes its environment (`Connector.probe`): whether it can be used on this Mac with these settings, which
`Capability`s it has, the versions it found, and why not when it cannot. SimMirror chooses one
(`registry.ConnectorRegistry.select`) and attaches it to a booted device (`Connector.attach`), getting a
`DeviceSession` with the roles the core drives -- each present only when the connector has the capabilities behind it:

* `ScreenSource` -- the screen's size, screenshots, and with ``STREAM_H264`` an H.264 stream;
* `InputSink` -- touches, buttons and keys, one stream per gesture (the ``INPUT_*`` capabilities);
* `ScreenReader` -- what is on screen, as an accessibility document (``ELEMENT_TREE``).

Booting, installing and launching are simctl's whichever connector is in use (`sim_mirror.platform.simctl`), so they
are not roles here.

Coordinates are the device's **points**, in portrait: what the accessibility tree reports and what a touch takes. A
screenshot's size is in pixels. `Screen` holds both and the scale between them.
"""

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from sim_mirror.config.model import SimConfig


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


#: The capabilities that need an `InputSink`.
INPUT_CAPABILITIES = frozenset(
    {Capability.INPUT_TOUCH, Capability.INPUT_BUTTON, Capability.INPUT_KEY, Capability.INPUT_TEXT}
)

Phase = Literal["down", "up"]

#: The hardware buttons a device has.
BUTTONS = ("home", "lock", "side", "siri", "apple_pay")


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


class ConnectorUnavailable(ConnectorError):
    """A connector that cannot reach a device, with the HTTP status that means."""

    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


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


class ScreenReader(Protocol):
    async def accessibility(self) -> dict[str, Any]:
        """What is on screen: the consolidated accessibility document, interactable elements only."""
        ...


@dataclass(frozen=True)
class ConnectorReport:
    """What a connector found when it looked at this Mac."""

    name: str
    available: bool
    capabilities: frozenset[Capability] = frozenset()
    versions: Mapping[str, str] = field(default_factory=dict)
    #: Why it cannot be used; empty when it can.
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "available": self.available,
            "capabilities": sorted(capability.value for capability in self.capabilities),
            "versions": dict(self.versions),
            "reasons": list(self.reasons),
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
    #: The most frames a second this connector can stream the screen at; None when there is no limit of its own.
    fps_limit: int | None = None
    #: Whether what the session relies on -- a helper process -- is still running.
    is_alive: Callable[[], bool] = _always
    on_close: Callable[[], Awaitable[None]] | None = None
    _closed: bool = field(default=False, init=False)

    @property
    def alive(self) -> bool:
        return not self._closed and self.is_alive()

    def can(self, capability: Capability) -> bool:
        return capability in self.capabilities

    async def close(self) -> None:
        """Let go of the device. The device itself keeps running. Closing twice is closing once."""
        if self._closed:
            return
        self._closed = True
        if self.on_close is not None:
            await self.on_close()


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
