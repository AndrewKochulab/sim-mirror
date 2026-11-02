# SPDX-License-Identifier: Apache-2.0
"""A macOS app's window, through a helper bound to its process. No global-event fallback exists."""

from __future__ import annotations

from collections.abc import AsyncIterable
from pathlib import Path
from typing import Any

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


class MacInput:
    def __init__(self, client: HelperClient, listing: AppListing | None = None, window_id: int = 0) -> None:
        self.client = client
        self.listing = listing
        self.window_id = window_id

    async def mac_action(self, action: dict[str, Any]) -> dict[str, Any]:
        if action.get("kind") == "type":
            if action.get("submit") and self.listing is None:
                raise ConnectorError("submitting needs SimMirrorKit or the debug web bridge; text was not changed")
            if "x" in action and "y" in action:
                await self.mac_action({"kind": "tap", "x": action["x"], "y": action["y"]})
        if self.listing is not None:
            return await send_input(self.listing, action, window_id=self.window_id)
        return await self.client.mac_action(action)

    async def accessibility(self) -> dict[str, Any]:
        if self.listing is None:
            return await self.client.accessibility()
        raw = await fetch_hierarchy(self.listing, max_nodes=3000, timeout_s=3, window_id=self.window_id)
        return document_from_app(raw, bundle_id=self.listing.bundle_id, pid=self.listing.pid, max_nodes=3000).document

    async def hid(self, events: AsyncIterable[HidEvent]) -> None:
        first: tuple[float, float] | None = None
        moved = False
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
                else:
                    moved = moved or point != first
            elif first is not None:
                if moved or point != first:
                    raise ConnectorError(
                        "dragging is unsupported by this app input adapter; no mouse events were posted"
                    )
                await self.mac_action({"kind": "tap", "x": event.x, "y": event.y})
                first = None

    async def type(self, text: str) -> None:
        await self.mac_action({"kind": "type", "text": text})


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
        running = await self.launcher.start_window(
            found.binary, udid, app.pid, window_id=self.mac.windows.get(udid), accessibility=config.mac_accessibility
        )
        client = running.engine
        try:
            info = await client.mac_action({"kind": "info"})
            listing = find_mac_listing(Path.home(), app.bundle_id, app.pid)
            input = MacInput(client, listing, int(info["window_id"]))
        except BaseException:
            await self.launcher.stop(running)
            raise
        enabled = config.mac_accessibility or listing is not None

        async def close() -> None:
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
            is_alive=lambda: running.alive,
            on_close=close,
            note="App-scoped SimMirrorKit input."
            if listing
            else "App-scoped accessibility. Custom gestures need SimMirrorKit; global input is never used.",
        )

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
