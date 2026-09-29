# SPDX-License-Identifier: Apache-2.0
"""Whether a scope's real device can be touched, and a person setting that up -- the viewer's Set up touch.

A real device is touched, typed on and read through WebDriverAgent, built on this Mac with the team the scope signs with
(`core.signing`) and set up once by a person (`connectors.iphone.wda_setup`). This says how that stands for the scope's
device (`TouchSetup` in the protocol), and sets it up when a person asks: building it when it is not built for the
device, or starting it again when it is -- after the person answered what the device asked, say. Either way the device
is attached again once WebDriverAgent can be started, and its viewers are told what it can do now.
"""

from __future__ import annotations

from sim_mirror.connectors.base import Capability
from sim_mirror.connectors.iphone.wda_setup import WdaSetup
from sim_mirror.core.instance import DeviceInstance
from sim_mirror.core.manager import DeviceManager
from sim_mirror.core.signing import SigningTeam, SigningTeams
from sim_mirror.host_copy import HostCopy
from sim_mirror.protocol import TouchSetup, TouchSetupState
from sim_mirror.scope import Scope
from sim_mirror.seams import ConfigSource


class TouchRefused(Exception):
    """A setup that cannot be started, said so a person knows what to do instead."""


class TouchSetups:
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

    def _device(self, scope: Scope) -> DeviceInstance | None:
        instance = self._manager.instance(scope)
        return instance if instance is not None and instance.kind == "physical" else None

    async def status(self, scope: Scope) -> TouchSetup:
        """How touching the scope's real device stands, and what a person can do about it."""
        instance = self._device(scope)
        if instance is None:
            return _said("not_needed")
        config = self._config.get(scope)
        if not config.wda_enabled:
            return _said("off", message=self._copy.wda_off())
        if Capability.INPUT_TOUCH in instance.capabilities:
            return _said("ready")
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

    async def set_up(self, scope: Scope) -> TouchSetup:
        """Set touching the scope's real device up: build WebDriverAgent for it, or start it again when it is built."""
        instance = self._device(scope)
        if instance is None:
            raise TouchRefused("Set up touch is for a real device; a simulator is touched as it is.")
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
        return await self.status(scope)

    async def shutdown(self) -> None:
        """End every setup under way."""
        await self._setup.shutdown()


def _said(state: TouchSetupState, *, message: str = "", found: SigningTeam | None = None) -> TouchSetup:
    return {
        "state": state,
        "message": message,
        "team": found.team if found else None,
        "team_from": found.source if found else None,
    }
