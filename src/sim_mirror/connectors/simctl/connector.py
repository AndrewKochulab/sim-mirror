# SPDX-License-Identifier: Apache-2.0
"""The simctl connector: a device's screen with nothing but Xcode, view-only.

It needs only xcrun, so it is what ``auto`` falls back to on a Mac with neither the native helper nor idb_companion:
the screen as JPEG, a few frames a second, and everything simctl does -- booting, installing, launching, opening a URL,
the appearance, logs -- but no touches, no keys and no accessibility tree. A viewer shows it as a mirror, and the agent
tools that need to touch or read the screen say which connector would.
"""

from __future__ import annotations

from collections.abc import Callable

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import Capability, ConnectorReport, ConnectorUnavailable, DeviceSession
from sim_mirror.connectors.registry import ConnectorContext
from sim_mirror.connectors.simctl.capture import SimctlScreen
from sim_mirror.platform.simctl import Simctl
from sim_mirror.platform.xcrun import xcrun_binary

NAME = "simctl"
CAPABILITIES = frozenset(
    {
        Capability.LIFECYCLE,
        Capability.DEVICE_LIST,
        Capability.APPEARANCE,
        Capability.OPEN_URL,
        Capability.APP_INSTALL,
        Capability.APP_LAUNCH,
        Capability.LOGS,
        Capability.SCREENSHOT,
        Capability.STREAM_JPEG,
    }
)
#: How often a whole screenshot can be taken and sent: more is wasted work.
FPS_LIMIT = 4
NO_XCRUN = "Xcode command-line tools are not installed (no xcrun)."


def _has_xcrun() -> bool:
    return xcrun_binary() is not None


class SimctlConnector:
    name = NAME

    def __init__(
        self,
        simctl_for: Callable[[str], Simctl],
        *,
        has_xcrun: Callable[[], bool] = _has_xcrun,
        screen_for: Callable[[Simctl, str], SimctlScreen] = SimctlScreen,
    ) -> None:
        self._simctl_for = simctl_for
        self._has_xcrun = has_xcrun
        self._screen_for = screen_for

    async def probe(self, config: SimConfig) -> ConnectorReport:
        if not self._has_xcrun():
            return ConnectorReport(NAME, False, reasons=(NO_XCRUN,))
        return ConnectorReport(NAME, True, CAPABILITIES)

    async def attach(self, udid: str, config: SimConfig) -> DeviceSession:
        if not self._has_xcrun():
            raise ConnectorUnavailable(NO_XCRUN, 409)
        screen = self._screen_for(self._simctl_for(config.developer_dir), udid)
        return DeviceSession(connector=NAME, capabilities=CAPABILITIES, screen=screen, fps_limit=FPS_LIMIT)

    async def reap_orphans(self) -> int:
        return 0


def create(context: ConnectorContext) -> SimctlConnector:
    return SimctlConnector(context.simctl_for)
