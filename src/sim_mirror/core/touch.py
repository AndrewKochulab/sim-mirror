# SPDX-License-Identifier: Apache-2.0
"""Whether a scope's real device can be touched, and a person setting that up -- the viewer's Set up touch.

What sets touch up depends on the kind of device (`TouchSetupKind`), and `TouchSetups` asks the one for the scope's.
A real iPhone is touched, typed on and read through WebDriverAgent, built on this Mac with the team the scope signs with
(`core.signing`) and set up once by a person (`connectors.iphone.wda_setup`). This says how that stands for the scope's
device (`TouchSetup` in the protocol), and sets it up when a person asks: building it when it is not built for the
device, or starting it again when it is -- after the person answered what the device asked, say. Either way the device
is attached again once WebDriverAgent can be started, and its viewers are told what it can do now. What last kept it
from driving the device, with when, is said beside how it stands (`DeviceManager.last_error`), for
``sim-mirror wda status`` and the doctor.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from sim_mirror.connectors.android.scrcpy_source import ScrcpyJars
from sim_mirror.connectors.base import Capability
from sim_mirror.connectors.iphone.wda_setup import WdaSetup
from sim_mirror.core.instance import DeviceInstance
from sim_mirror.core.manager import DeviceManager
from sim_mirror.core.signing import SigningTeam, SigningTeams
from sim_mirror.host_copy import HostCopy
from sim_mirror.protocol import DeviceKind, TouchSetup, TouchSetupState
from sim_mirror.scope import Scope
from sim_mirror.seams import ConfigSource


class TouchRefused(Exception):
    """A setup that cannot be started, said so a person knows what to do instead."""


class TouchSetupKind(Protocol):
    """What sets touch up for one kind of device."""

    async def status(self, scope: Scope, instance: DeviceInstance) -> TouchSetup:
        """How touching this device stands, and what a person can do about it."""
        ...

    async def set_up(self, scope: Scope, instance: DeviceInstance) -> TouchSetup:
        """Set touching this device up, as a person asked. Raises `TouchRefused` when it cannot be started."""
        ...

    async def shutdown(self) -> None:
        """End every setup under way."""
        ...


class TouchSetups:
    """A scope's Set up touch, asked of what sets touch up for the kind of its device: WebDriverAgent for an iPhone,
    and those given for other kinds. A simulator is touched as it is."""

    def __init__(
        self,
        manager: DeviceManager,
        config: ConfigSource,
        signing: SigningTeams,
        setup: WdaSetup,
        copy: HostCopy | None = None,
        *,
        others: Mapping[DeviceKind, TouchSetupKind] | None = None,
    ) -> None:
        self._manager = manager
        self._kinds: dict[DeviceKind, TouchSetupKind] = {
            "physical": WdaTouchSetup(manager, config, signing, setup, copy),
            **(others or {}),
        }

    def _device(self, scope: Scope) -> tuple[DeviceInstance, TouchSetupKind] | None:
        instance = self._manager.instance(scope)
        kind = self._kinds.get(instance.kind) if instance is not None else None
        return None if instance is None or kind is None else (instance, kind)

    async def status(self, scope: Scope) -> TouchSetup:
        """How touching the scope's device stands, and what a person can do about it."""
        found = self._device(scope)
        return _said("not_needed") if found is None else await found[1].status(scope, found[0])

    async def set_up(self, scope: Scope) -> TouchSetup:
        """Set touching the scope's device up, as a person asked."""
        found = self._device(scope)
        if found is None:
            raise TouchRefused("Set up touch is for a real device; a simulator is touched as it is.")
        return await found[1].set_up(scope, found[0])

    async def shutdown(self) -> None:
        """End every setup under way."""
        for kind in self._kinds.values():
            await kind.shutdown()


class WdaTouchSetup:
    """A real iPhone's touch: WebDriverAgent, built with the scope's team and started on the device."""

    def __init__(
        self,
        manager: DeviceManager,
        config: ConfigSource,
        signing: SigningTeams,
        setup: WdaSetup,
        copy: HostCopy | None = None,
    ) -> None:
        self._manager = manager
        self._config = config
        self._signing = signing
        self._setup = setup
        self._copy = copy or HostCopy()

    async def status(self, scope: Scope, instance: DeviceInstance) -> TouchSetup:
        """How touching the scope's real device stands, what a person can do about it, and what last kept
        WebDriverAgent from driving it."""
        said = await self._status(scope, instance)
        return {**said, "last_error": self._manager.last_error(instance.udid)}

    async def _status(self, scope: Scope, instance: DeviceInstance) -> TouchSetup:
        config = self._config.get(scope)
        if not config.wda_enabled:
            return _said("off", message=self._copy.wda_off())
        if Capability.INPUT_TOUCH in instance.capabilities:
            # Set up and started is not answering: one that stopped answering -- the device locked, its cable loose --
            # is said so, rather than ready.
            session = instance.session
            silent = await session.answers() if session is not None and session.answers is not None else None
            return _said("ready") if silent is None else _said("failed", message=silent)
        found = await self._signing.of(scope, config)
        if found is None:
            return _said("needs_team", message=self._copy.wda_needs_team())
        if instance.recovery is not None and not instance.recovery.done():
            return _said("starting", message="Starting WebDriverAgent on the device.", found=found)
        setup = self._setup.state(found.team, instance.developer_dir)
        if setup is not None and setup.state == "building":
            return _said("building", message=self._copy.wda_building(found.team), found=found)
        if setup is not None and setup.state == "failed":
            return _said("failed", message=self._copy.wda_setup_failed(setup.said), found=found)
        if self._setup.built_for(found.team, instance.developer_dir, instance.udid) is not None:
            return _said("failed", message=instance.fallback_reason or "", found=found)
        return _said("offer", message=self._copy.wda_offer(instance.udid), found=found)

    async def set_up(self, scope: Scope, instance: DeviceInstance) -> TouchSetup:
        """Set touching the scope's real device up: build WebDriverAgent for it, or start it again when it is built."""
        config = self._config.get(scope)
        if not config.wda_enabled:
            raise TouchRefused(self._copy.wda_off())
        found = await self._signing.of(scope, config)
        if found is None:
            raise TouchRefused(self._copy.wda_needs_team())
        if self._setup.built_for(found.team, instance.developer_dir, instance.udid) is not None:
            self._manager.reattach(instance.udid)
        else:
            self._setup.start(found.team, instance.developer_dir, instance.udid, config.wda_path)
        return await self.status(scope, instance)

    async def shutdown(self) -> None:
        """End every setup under way."""
        await self._setup.shutdown()


class ScrcpyTouchSetup:
    """An Android device's live screen and quick touch: scrcpy's server, fetched once and checked
    (`connectors.android.scrcpy_source`). Until then the device is shown and touched through adb alone."""

    def __init__(self, manager: DeviceManager, config: ConfigSource, jars: ScrcpyJars) -> None:
        self._manager = manager
        self._config = config
        self._jars = jars

    async def status(self, scope: Scope, instance: DeviceInstance) -> TouchSetup:
        config = self._config.get(scope)
        if not config.scrcpy:
            return _said("off", message=SCRCPY_OFF)
        if Capability.STREAM_H264 in instance.capabilities:
            return _said("ready")
        fetching = self._jars.fetching
        if fetching is not None and fetching.state == "fetching":
            return _said("building", message=fetching.said)
        if fetching is not None and fetching.state == "failed":
            return _said("failed", message=fetching.said)
        if self._jars.find(config.scrcpy_path) is None:
            why = self._jars.why_not(config.scrcpy_path) if config.scrcpy_path else self._offer()
            return _said("offer", message=why or "")
        if instance.recovery is not None and not instance.recovery.done():
            return _said("starting", message="Starting scrcpy's server on the device.")
        return _said("failed", message=instance.fallback_reason or "")

    def _offer(self) -> str:
        """What setting it up does, said before a person does it: it fetches something from the internet."""
        release = self._jars.release
        return (
            "Without scrcpy the screen is shown a few times a second and touches are slow. Setting it up fetches "
            f"scrcpy's server {release.version} from GitHub (Genymobile, Apache-2.0), checks it against the hash "
            "SimMirror pins, and shows this device live."
        )

    async def set_up(self, scope: Scope, instance: DeviceInstance) -> TouchSetup:
        config = self._config.get(scope)
        if not config.scrcpy:
            raise TouchRefused(SCRCPY_OFF)
        if self._jars.find(config.scrcpy_path) is not None:
            self._manager.reattach(instance.udid)
        elif config.scrcpy_path:
            raise TouchRefused(self._jars.why_not(config.scrcpy_path) or "")
        else:
            self._jars.start()
        return await self.status(scope, instance)

    async def shutdown(self) -> None:
        await self._jars.shutdown()


SCRCPY_OFF = (
    "scrcpy is off here (android.scrcpy.enabled): the device is shown a screenshot at a time and touched through adb."
)


def _said(state: TouchSetupState, *, message: str = "", found: SigningTeam | None = None) -> TouchSetup:
    return {
        "state": state,
        "message": message,
        "team": found.team if found else None,
        "team_from": found.source if found else None,
        "last_error": None,
    }
