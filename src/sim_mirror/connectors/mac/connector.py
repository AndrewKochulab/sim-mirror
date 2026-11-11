# SPDX-License-Identifier: Apache-2.0
"""A macOS app's window, through a helper bound to its process. No global-event fallback exists."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterable, Mapping
from pathlib import Path
from typing import Any, Protocol

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.app.client import fetch_hierarchy, send_input
from sim_mirror.connectors.app.discovery import AppListing, find_mac_listing
from sim_mirror.connectors.app.document import document_from_app
from sim_mirror.connectors.base import (
    Capability,
    ConnectorError,
    ConnectorReport,
    ConnectorUnavailable,
    DeviceSession,
    HidEvent,
)
from sim_mirror.connectors.native.client import HelperClient
from sim_mirror.connectors.native.connector import default_candidates
from sim_mirror.connectors.native.helper import CachedVersions, HelperLauncher, locate_helper
from sim_mirror.connectors.registry import ConnectorContext
from sim_mirror.core.gestures import KEYS
from sim_mirror.platform.macos import MacOS
from sim_mirror.protocol import DeviceKind

CAPABILITIES = frozenset(
    {
        Capability.LIFECYCLE,
        Capability.DEVICE_LIST,
        Capability.APP_INSTALL,
        Capability.APP_LAUNCH,
        Capability.LOGS,
        Capability.SCREENSHOT,
        Capability.STREAM_JPEG,
        Capability.STREAM_H264,
        Capability.RECORD,
    }
)
INPUT = frozenset({Capability.INPUT_POINTER, Capability.INPUT_TOUCH, Capability.INPUT_TEXT, Capability.ELEMENT_TREE})


class MacWindows:
    def __init__(self, client: HelperClient, mac: MacOS, udid: str) -> None:
        self.client, self.mac, self.udid = client, mac, udid

    async def windows(self) -> dict[str, Any]:
        return await self.client.mac_action({"kind": "info"})

    async def select_window(self, target: int | str) -> None:
        found = [
            window
            for window in (await self.windows())["windows"]
            if window["id"] == target or window["title"] == target
        ]
        if len(found) != 1:
            raise ConnectorError("choose one window ID or a unique title from this app's windows")
        self.mac.windows[self.udid] = int(found[0]["id"])


#: The most points one in-process drag carries; a longer path is thinned evenly, keeping its ends.
DRAG_POINTS = 64


def thinned(points: list[tuple[float, float]], most: int = DRAG_POINTS) -> list[tuple[float, float]]:
    """`points`, at most `most` of them, spread evenly along the path and always keeping the first and the last."""
    if len(points) <= most:
        return points
    step = (len(points) - 1) / (most - 1)
    return [points[round(index * step)] for index in range(most)]


#: How long an app has to answer one command, before any time the command itself takes.
ANSWER_S = 3.0


def _answer_s(action: dict[str, Any]) -> float:
    """How long to wait for the app's answer: a held click or a drag takes its own duration first."""
    ms = action.get("ms")
    return ANSWER_S + (ms / 1000 if isinstance(ms, int) and 0 < ms <= 10000 else 0)


class MacInput:
    def __init__(
        self,
        client: HelperClient,
        listing: AppListing | None = None,
        window_id: int = 0,
        *,
        ax_menus: bool = False,
    ) -> None:
        self.client = client
        self.listing = listing
        self.window_id = window_id
        #: Whether the app's menu bar can be chosen through its accessibility actions.
        self.ax_menus = ax_menus

    def offers(self, name: str) -> bool:
        """Whether the app's adapter says it can do `name`."""
        return self.listing is not None and self.listing.capabilities is not None and name in self.listing.capabilities

    @property
    def gestures(self) -> bool:
        return self.offers("gestures")

    @property
    def multi_click(self) -> bool:
        """Whether a tap may click several times (``clicks``): the CEF adapter has since it shipped, saying so first
        only as trusted input."""
        return self.offers("clicks") or self.offers("trusted_input")

    @property
    def modifier_input(self) -> bool:
        """Whether a click, a drag, a hover or a scroll may hold modifier keys (``modifiers``)."""
        return self.offers("modifiers")

    @property
    def middle_click(self) -> bool:
        return self.offers("middle_click")

    async def mac_action(self, action: dict[str, Any]) -> dict[str, Any]:
        if self.listing is None and (action.get("kind") == "middle_click" or {"clicks", "modifiers"} & set(action)):
            # Accessibility has no click count, middle button or held key: refused rather than pressed once.
            raise ConnectorError("accessibility cannot click twice, click the middle button or hold a modifier key")
        if action.get("kind") == "type":
            if action.get("submit") and self.listing is None:
                raise ConnectorError("submitting needs SimMirrorKit or the debug web bridge; text was not changed")
            if "x" in action and "y" in action:
                await self.mac_action({"kind": "tap", "x": action["x"], "y": action["y"]})
        if self.listing is not None:
            listed = self.listing.capabilities
            if action.get("kind") == "menu" and listed is not None and "menu" not in listed and self.ax_menus:
                # An adapter that says it has no menu hook leaves the app's menu bar to its accessibility actions.
                return await self.client.mac_action(action)
            return await send_input(self.listing, action, window_id=self.window_id, timeout_s=_answer_s(action))
        return await self.client.mac_action(action)

    async def accessibility(self) -> dict[str, Any]:
        if self.listing is None:
            return await self.client.accessibility()
        raw = await fetch_hierarchy(self.listing, max_nodes=3000, timeout_s=3, window_id=self.window_id)
        return document_from_app(raw, bundle_id=self.listing.bundle_id, pid=self.listing.pid, max_nodes=3000).document

    async def hid(self, events: AsyncIterable[HidEvent]) -> None:
        first: tuple[float, float] | None = None
        moved = False
        path: list[tuple[float, float]] = []
        async for event in events:
            if event.kind == "key" and self.listing is not None:
                name = next((name for name, code in KEYS.items() if code == event.code), None)
                if name is None:
                    raise ConnectorError("this key sequence is unsupported; use a macOS keys chord")
                if event.phase == "down":
                    await self.mac_action({"kind": "keys", "text": name})
                continue
            if event.kind != "touch":
                raise ConnectorError(
                    "keys and hardware buttons need in-process support; this app exposes accessibility actions"
                )
            point = (event.x, event.y)
            if event.phase == "down":
                if first is None:
                    first = point
                    path = [point]
                else:
                    moved = moved or point != first
                    path.append(point)
            elif first is not None:
                if moved or point != first:
                    if not self.gestures:
                        raise ConnectorError(
                            "dragging is unsupported by this app input adapter; no mouse events were posted"
                        )
                    points = thinned([*path, point])
                    await self.mac_action({"kind": "drag", "points": [list(p) for p in points], "ms": 300})
                else:
                    await self.mac_action({"kind": "tap", "x": event.x, "y": event.y})
                first = None
                moved = False
                path = []

    async def type(self, text: str) -> None:
        await self.mac_action({"kind": "type", "text": text})


class OverlayClient(Protocol):
    async def overlay(self, event: Mapping[str, Any]) -> None: ...


#: The most agent events waiting for the overlay; past it a new one is dropped rather than held up behind them.
OVERLAY_QUEUE = 64


class MacPresence:
    """The agent drawn over the app's window on this Mac, by the helper's overlay (`connectors.base.Presence`).

    Events go to the helper in the order they happened, one at a time, behind the gesture that announced them rather
    than in its way: `show` only queues. One the helper cannot take is dropped -- a pointer missing from the Mac's
    screen never fails what the agent does."""

    def __init__(self, client: OverlayClient) -> None:
        self.client = client
        self._queue: asyncio.Queue[Mapping[str, Any]] = asyncio.Queue(OVERLAY_QUEUE)
        self._task: asyncio.Task[None] | None = None
        self._closed = False

    def show(self, event: Mapping[str, Any]) -> None:
        if self._closed:
            return
        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(event)
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._drain())

    async def _drain(self) -> None:
        while True:
            event = await self._queue.get()
            with contextlib.suppress(ConnectorError):
                await self.client.overlay(event)

    def close(self) -> None:
        """Stop sending: the helper, and its window, are going."""
        self._closed = True
        if self._task is not None:
            self._task.cancel()


def _note(listing: AppListing | None) -> str:
    """What drives this app, as the session says it."""
    if listing is None:
        return "App-scoped accessibility. Custom gestures need SimMirrorKit; global input is never used."
    if listing.capabilities is not None and "trusted_input" in listing.capabilities:
        return "App-scoped trusted input through the app's debug bridge; global input is never used."
    return "App-scoped SimMirrorKit input."


class MacConnector:
    name = "mac-app"
    kinds: frozenset[DeviceKind] = frozenset({"mac"})

    def __init__(self, launcher: HelperLauncher, mac: MacOS) -> None:
        self.launcher = launcher
        self.mac = mac
        self.versions = CachedVersions()

    async def probe(self, config: SimConfig) -> ConnectorReport:
        if not config.mac_apps:
            return ConnectorReport(
                self.name, False, reasons=("macOS apps are off here (mac.apps.enabled)",), kinds=self.kinds
            )
        found = await locate_helper(config.native_helper_path, default_candidates(), self.versions)
        if not found.usable or found.version is None or "mac-capture" not in found.version.features:
            return ConnectorReport(
                self.name, False, reasons=("build or install the helper with mac-capture support",), kinds=self.kinds
            )
        return ConnectorReport(
            self.name, True, CAPABILITIES | (INPUT if config.mac_accessibility else frozenset()), kinds=self.kinds
        )

    async def attach(self, udid: str, config: SimConfig) -> DeviceSession:
        found = await locate_helper(config.native_helper_path, default_candidates(), self.versions)
        if not found.usable or found.binary is None:
            raise ConnectorUnavailable("the macOS helper is unavailable", 409)
        app = await self.mac.app(udid)
        if app.pid is None:
            raise ConnectorUnavailable("the target app is not running", 409)
        pid = app.pid
        # The device follows the process this session is attached to, which may be a new one: the app replaced.
        self.mac.held[udid] = pid
        chosen = self.mac.windows.get(udid)
        # Only a helper that can draw over the app is asked to; an older one would refuse the flag.
        overlay = config.mac_overlay and found.version is not None and "mac-overlay" in found.version.features
        try:
            running = await self.launcher.start_window(
                found.binary, udid, pid, window_id=chosen, accessibility=config.mac_accessibility, overlay=overlay
            )
        except ConnectorError:
            if chosen is None:
                raise
            # The window chosen earlier has ended: the app's main window is chosen afresh rather than refused forever.
            self.mac.windows.pop(udid, None)
            running = await self.launcher.start_window(
                found.binary, udid, pid, window_id=None, accessibility=config.mac_accessibility, overlay=overlay
            )
        client = running.engine
        try:
            info = await client.mac_action({"kind": "info"})
            listing = find_mac_listing(Path.home(), app.bundle_id, app.pid)
            input = MacInput(client, listing, int(info["window_id"]), ax_menus=config.mac_accessibility)
        except BaseException:
            await self.launcher.stop(running)
            raise
        enabled = config.mac_accessibility or listing is not None
        presence = MacPresence(client) if overlay else None

        async def close() -> None:
            if presence is not None:
                presence.close()
            await self.launcher.stop(running)

        return DeviceSession(
            connector=self.name,
            capabilities=CAPABILITIES
            | (INPUT if enabled else frozenset())
            | (frozenset({Capability.INPUT_KEY}) if listing else frozenset()),
            screen=client,
            input=input if enabled else None,
            text=input if enabled else None,
            reader=input if enabled else None,
            points=client if config.mac_accessibility else None,
            buttons=(),
            window_control=MacWindows(client, self.mac, udid),
            presence=presence,
            is_alive=lambda: running.alive and self._follows(udid, pid),
            on_close=close,
            note=_note(listing),
        )

    def _follows(self, udid: str, pid: int) -> bool:
        """Whether the device still follows the process a session was attached to, as LaunchServices last listed it:
        once that copy has ended -- the app replaced by a new build -- the session is over, and the next call attaches
        the new process and a window chosen afresh."""
        known = self.mac.known.get(udid)
        return known is None or known.pid == pid

    async def reap_orphans(self) -> int:
        return await self.launcher.reap_orphans()


def create(context: ConnectorContext) -> MacConnector:
    return MacConnector(
        HelperLauncher(
            run_dir=context.state.run_dir(),
            log_dir=context.state.log_dir(),
            owner_tag=context.state.owner_tag,
            copy=context.copy,
        ),
        context.mac or MacOS(),
    )
