# SPDX-License-Identifier: Apache-2.0
"""The iphone connector: a real iPhone or iPad connected to the Mac.

It is the only connector a real device has, and it chooses how to reach the device each time it attaches, from what
is there now (`real_devices.screen`): the live screen over its cable, read by the native helper (`capture`), else a
screenshot about once a second through devicectl, with no cable and nothing installed. What a session can do is what
the device says it can -- devicectl lists each device's features -- so a device that cannot, say, simulate a place is
never offered it. Its note says what would let it do more.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import (
    Capability,
    ConnectorReport,
    ConnectorUnavailable,
    DeviceSession,
)
from sim_mirror.connectors.iphone.cable import Cable, UsbCable
from sim_mirror.connectors.iphone.capture import CableCapture, LiveScreen, LiveScreens
from sim_mirror.connectors.iphone.screen import FPS_LIMIT, DevicectlScreen
from sim_mirror.connectors.native.connector import default_candidates
from sim_mirror.connectors.native.helper import HelperLauncher
from sim_mirror.connectors.registry import ConnectorContext
from sim_mirror.connectors.simctl.connector import NO_XCRUN
from sim_mirror.core.device_logs import DeviceLogBook
from sim_mirror.host_copy import HostCopy
from sim_mirror.platform.devicectl import Devicectl, DevicectlError, Display, PhysicalDevice
from sim_mirror.platform.errors import DeviceControlError
from sim_mirror.platform.xcrun import xcrun_binary
from sim_mirror.protocol import DeviceKind

logger = logging.getLogger(__name__)

NAME = "iphone"
KINDS: frozenset[DeviceKind] = frozenset({"physical"})
#: What a device can be offered by what it says it can do, by devicectl's feature names.
FEATURES: Mapping[str, frozenset[Capability]] = {
    "capturescreenshot": frozenset({Capability.SCREENSHOT, Capability.STREAM_JPEG, Capability.RECORD}),
    "installapp": frozenset({Capability.APP_INSTALL}),
    "launchapplication": frozenset({Capability.APP_LAUNCH, Capability.OPEN_URL}),
    "customizeuistyle": frozenset({Capability.APPEARANCE}),
    "customizeappearancesettings": frozenset({Capability.ACCESSIBILITY}),
    "simulatelocation": frozenset({Capability.LOCATION}),
    "simulateStatusBar": frozenset({Capability.STATUS_BAR}),
}
#: What any real device is offered: it is listed, picked and brought up.
ALWAYS = frozenset({Capability.LIFECYCLE, Capability.DEVICE_LIST})
#: The most a real device can be offered, as the connector reports it before it knows the device.
MOST = frozenset(Capability) - {Capability.BUILD_PREVIEW}
#: What a device whose cable shows its screen is offered besides: the helper serves screenshots, a live picture, and
#: so recordings, whatever devicectl says.
LIVE = frozenset({Capability.SCREENSHOT, Capability.STREAM_JPEG, Capability.STREAM_H264, Capability.RECORD})
#: The `real_devices.screen` choices that read a cabled device's screen over its cable.
CABLE_MODES = frozenset({"auto", "usb"})


def capabilities_of(device: PhysicalDevice) -> frozenset[Capability]:
    """What this device can be offered through devicectl alone, by the features it says it has."""
    offered = set(ALWAYS)
    for feature, capabilities in FEATURES.items():
        if feature in device.features:
            offered |= capabilities
    return frozenset(offered)


class IPhoneConnector:
    """Reaches a real device through devicectl."""

    name = NAME
    #: The kinds of device it drives, known before it is probed.
    kinds = KINDS

    def __init__(
        self,
        devicectl_for: Callable[[str], Devicectl],
        *,
        copy: HostCopy | None = None,
        has_xcrun: Callable[[], bool] = lambda: xcrun_binary() is not None,
        cable: Cable | None = None,
        logs: DeviceLogBook | None = None,
        screens: LiveScreens | None = None,
    ) -> None:
        self._devicectl_for = devicectl_for
        self._copy = copy or HostCopy()
        self._has_xcrun = has_xcrun
        #: The device's cable, when one can be asked about; None where only devicectl is used.
        self._cable = cable
        #: Where each cabled device's log is kept, for its control to read; None where no log is kept.
        self._logs = logs
        #: What shows a cabled device's live screen; None where only screenshots are.
        self._screens = screens

    @staticmethod
    def _xcode(config: SimConfig) -> str:
        return config.real_devices_developer_dir or config.developer_dir

    async def probe(self, config: SimConfig) -> ConnectorReport:
        if not config.real_devices:
            return ConnectorReport(NAME, False, reasons=(self._copy.kind_unavailable("physical"),), kinds=KINDS)
        if not self._has_xcrun():
            return ConnectorReport(NAME, False, reasons=(NO_XCRUN,), kinds=KINDS)
        return ConnectorReport(NAME, True, MOST, kinds=KINDS)

    async def attach(self, udid: str, config: SimConfig) -> DeviceSession:
        devicectl = self._devicectl_for(self._xcode(config))
        try:
            known = await devicectl.device(udid)
            if known is None or not known.connected:
                raise ConnectorUnavailable(self._copy.not_connected(known.name if known else udid), 409)
            # Asking the device something brings its tunnel up; only then does devicectl list everything it can do --
            # a handful of features while the tunnel is idle, dozens once it is up.
            display = await devicectl.display(udid)
            listed = await devicectl.devices()
        except DevicectlError as exc:
            raise ConnectorUnavailable(str(exc)) from exc
        device = next((each for each in listed if each.udid == udid), known)
        capabilities = set(capabilities_of(device))
        cabled = self._cable is not None and await asyncio.to_thread(self._cable.cabled, udid)
        live, why = await self._live(device, display, config, devicectl, cabled=cabled, listed=listed)
        if cabled and await self._keep_log(udid, config):
            capabilities.add(Capability.LOGS)

        async def close() -> None:
            if live is not None:
                await live.close()
            if self._logs is not None:
                await asyncio.to_thread(self._logs.stop, udid)

        if live is None:
            return DeviceSession(
                connector=NAME,
                capabilities=frozenset(capabilities),
                screen=DevicectlScreen(devicectl, udid, display),
                fps_limit=FPS_LIMIT,
                note=self._copy.iphone_limits(cable=why),
                on_close=close,
            )
        return DeviceSession(
            connector=NAME,
            capabilities=frozenset(capabilities | LIVE),
            screen=live.screen,
            note=self._copy.iphone_limits(live=True),
            is_alive=live.alive,
            on_close=close,
        )

    async def _live(
        self,
        device: PhysicalDevice,
        display: Display,
        config: SimConfig,
        devicectl: Devicectl,
        *,
        cabled: bool,
        listed: Sequence[PhysicalDevice],
    ) -> tuple[LiveScreen | None, str | None]:
        """The device's screen over its cable, or None and why not -- no reason when the cable is not to be used.

        Refuses the device when its screen is to be read only over a cable that cannot show it.
        """
        mode = config.real_devices_screen
        if self._screens is None or mode not in CABLE_MODES:
            return None, None
        if not cabled:
            if mode == "usb":
                raise ConnectorUnavailable(self._copy.cable_only(device.name), 409)
            return None, None
        twins = any(
            other.udid != device.udid and other.name == device.name and other.connection == "usb" for other in listed
        )
        try:
            return await self._screens.open(device, display, config, devicectl, twins=twins), None
        except ConnectorUnavailable as exc:
            if mode == "usb":
                raise
            logger.info("the cable of %s does not show its screen: %s", device.udid, exc)
            return None, str(exc)

    async def _keep_log(self, udid: str, config: SimConfig) -> bool:
        """Start keeping a cabled device's log; whether it is kept. A device whose log cannot be read is still used."""
        if self._logs is None or self._cable is None:
            return False
        try:
            stream = await asyncio.to_thread(self._cable.open_log, udid)
        except DeviceControlError as exc:
            logger.info("the log of %s is not kept: %s", udid, exc)
            return False
        self._logs.start(udid, stream, max_bytes=config.real_devices_log_buffer_mb << 20)
        return True

    async def reap_orphans(self) -> int:
        return await self._screens.reap_orphans() if self._screens is not None else 0


def create(context: ConnectorContext) -> IPhoneConnector:
    """The iphone connector for a host: its cable helpers' sockets, pid files and logs in the host's folders."""
    state = context.state
    launcher = HelperLauncher(
        run_dir=state.run_dir(),
        log_dir=state.log_dir(),
        owner_tag=state.owner_tag,
        copy=context.copy,
        ensure_dir=state.ensure_dir,
    )
    return IPhoneConnector(
        context.devicectl_for,
        copy=context.copy,
        cable=UsbCable(),
        logs=context.device_logs,
        screens=CableCapture(launcher, candidates=default_candidates, copy=context.copy),
    )
