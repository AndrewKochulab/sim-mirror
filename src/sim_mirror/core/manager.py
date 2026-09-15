# SPDX-License-Identifier: Apache-2.0
"""Every scope's simulator, for one process.

A scope asks for its simulator (`ensure`); the answer comes at once, in state ``booting``, and the device is brought up
in the background -- claimed, booted if it was off, waited for until SpringBoard is up, attached to its connector,
described -- so a first boot that takes a minute never holds a request open. What happens next reaches every open screen
through the device's `EventBus`.

What keeps it contained:

* **one instance per device**, keyed by UDID, whichever scopes use it (``device.mode``), and one lock for every change
  to which devices run, so two scopes asking at once cannot bring the same device up twice;
* **a claim per device** (`storage.claims`), so another process on the Mac -- another host, or a second copy of this
  one -- is refused a device this one is driving, and told who has it;
* **``device.max_booted``**: asking for one more than that ends the least recently used device nobody watches, no agent
  holds and nothing is building on -- or refuses, saying why;
* **off means off**: `reconcile` runs when settings change, before the change is answered, and ends the devices of a
  scope that cannot have one now; a connector switched since brings the device back on the new one; `reap` does both
  once a minute, and ends devices left idle for ``device.idle_minutes``;
* **only what it booted or made is shut down**: ending a device a person had booted leaves it running, a device
  SimMirror made is shut down even after a restart forgot booting it, and nothing is ever deleted here.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import Connector, ConnectorError
from sim_mirror.core.availability import Availability
from sim_mirror.core.devices import DeviceDirectory, NoDevice
from sim_mirror.core.frames import FrameHub, StreamSettings
from sim_mirror.core.instance import FAILED, READY, STALLED, STOPPED, Closer, DeviceInstance
from sim_mirror.core.status import device_choices, scope_status
from sim_mirror.host_copy import HostCopy
from sim_mirror.platform.simctl import Simctl, SimctlError
from sim_mirror.protocol import CLOSE_FORBIDDEN, CLOSE_RESTARTING, CLOSE_STOPPED, DeviceChoice, ScopeStatus
from sim_mirror.scope import Scope
from sim_mirror.seams import ConfigSource, HeldDevice, UsageProbe
from sim_mirror.storage.claims import Claims, DeviceClaimed

logger = logging.getLogger(__name__)

#: How long a device whose connector stopped waits before each attempt to attach it again; then it has failed.
RESTART_S = (1.0, 5.0, 30.0)
#: How long a boot may take before it is given up: a first boot migrates data and can take minutes.
BOOT_TIMEOUT_S = 240.0
STOPPED_REASON = "the simulator stopped"
RESTARTING_REASON = "the simulator is restarting"


class SimulatorUnavailable(Exception):
    """Why a simulator could not be had, with the HTTP status it means."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


class NoUsage:
    """A `UsageProbe` for a host with no agents of its own to ask about."""

    def in_use(self, device: HeldDevice) -> bool:
        return False


class DeviceManager:
    """Every running device, for one process."""

    def __init__(
        self,
        *,
        config: ConfigSource,
        availability: Availability,
        directory: DeviceDirectory,
        claims: Claims,
        simctl_for: Callable[[str], Simctl],
        copy: HostCopy | None = None,
        usage: UsageProbe | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.availability = availability
        self.directory = directory
        #: Whether an agent holds a device; a host points it at its own sessions.
        self.usage: UsageProbe = usage or NoUsage()
        self._config = config
        self._claims = claims
        self._simctl_for = simctl_for
        self._copy = copy or HostCopy()
        self._clock = clock
        self._sleep = sleep
        self._instances: dict[str, DeviceInstance] = {}
        self._scopes: dict[str, str] = {}
        self._lock = asyncio.Lock()

    # -- what there is --------------------------------------------------------------------------------------------

    def instance(self, scope: Scope) -> DeviceInstance | None:
        return self._instances.get(self._scopes.get(scope.id, ""))

    def instances(self) -> list[DeviceInstance]:
        return list(self._instances.values())

    def now(self) -> float:
        return self._clock()

    async def unavailable(self, scope: Scope) -> str | None:
        """Why this scope cannot have a simulator right now, or None when it can."""
        return (await self.availability.check(scope)).reason

    async def status(self, scope: Scope) -> ScopeStatus:
        return scope_status(await self.availability.check(scope), self.instance(scope), self._clock())

    async def devices(self, scope: Scope) -> list[DeviceChoice]:
        """The iOS simulators on this Mac the scope could use, for a picker."""
        config = self._config.get(scope)
        try:
            found = await self._simctl_for(config.developer_dir).devices()
        except SimctlError as exc:
            raise SimulatorUnavailable(str(exc), 502) from exc
        return device_choices(found, self.directory.memory.created(scope))

    async def choose(self, scope: Scope, udid: str) -> None:
        """Use the simulator a person picked for this scope from now on, letting go of the one it had."""
        config = self._config.get(scope)
        try:
            device = await self._simctl_for(config.developer_dir).device(udid)
        except SimctlError as exc:
            raise SimulatorUnavailable(str(exc), 400) from exc
        if device is None or not device.available:
            raise SimulatorUnavailable("That simulator does not exist on this Mac.", 404)
        await self.stop(scope)
        self.directory.memory.choose(scope, config.device_mode == "shared", udid)

    # -- a device's life -------------------------------------------------------------------------------------------

    async def ensure(self, scope: Scope) -> DeviceInstance:
        """The scope's device: running, or being brought up. Refuses with a reason and a status."""
        verdict = await self.availability.check(scope)
        if verdict.reason or verdict.selection is None or verdict.connector is None:
            raise SimulatorUnavailable(verdict.reason or "No connector can reach a simulator here.")
        config, selection, connector = verdict.config, verdict.selection, verdict.connector
        async with self._lock:
            now = self._clock()
            current = self.instance(scope)
            if current is not None and current.live:
                current.last_used = now
                return current
            if current is not None:
                await self._end(current, shutdown=False)
            simctl = self._simctl_for(config.developer_dir)
            try:
                ref = await self.directory.resolve(simctl, scope, config)
            except NoDevice as exc:
                raise SimulatorUnavailable(str(exc), exc.status) from exc
            except SimctlError as exc:
                raise SimulatorUnavailable(str(exc), 502) from exc
            running = self._instances.get(ref.udid)
            if running is not None and running.live:
                running.scopes.add(scope.id)
                running.last_used = now
                self._scopes[scope.id] = ref.udid
                return running
            if running is not None:
                await self._end(running, shutdown=False)
            await self._make_room(config.max_booted)
            report = selection.report
            instance = DeviceInstance(
                udid=ref.udid,
                name=ref.name,
                runtime=ref.runtime,
                owner=scope,
                developer_dir=config.developer_dir,
                scopes={scope.id},
                created=ref.created,
                connector=connector.name,
                capabilities=report.capabilities if report is not None else frozenset(),
                fallback_reason=selection.fallback_reason,
                since=now,
                last_used=now,
            )
            self._instances[ref.udid] = instance
            self._scopes[scope.id] = ref.udid
            instance.task = asyncio.get_running_loop().create_task(self._bring_up(instance, simctl, config, connector))
            return instance

    async def _make_room(self, limit: int) -> None:
        while True:
            live = [instance for instance in self._instances.values() if instance.live]
            if len(live) < limit:
                return
            idle = sorted(
                (i for i in live if not i.viewers and not i.busy and not self.usage.in_use(i)),
                key=lambda instance: instance.last_used,
            )
            if not idle:
                raise SimulatorUnavailable(self._copy.too_many_booted(len(live), limit))
            await self._end(idle[0], shutdown=idle[0].may_shut_down)

    async def _bring_up(
        self, instance: DeviceInstance, simctl: Simctl, config: SimConfig, connector: Connector
    ) -> None:
        try:
            await self._claims.acquire(instance.udid)
            device = await simctl.device(instance.udid)
            if device is not None and not device.booted:
                await simctl.boot(instance.udid)
                instance.booted_by_us = True
            await simctl.bootstatus(instance.udid, timeout=BOOT_TIMEOUT_S)
            instance.session = await connector.attach(instance.udid, config)
            instance.screen = await instance.session.screen.describe()
            instance.hub = FrameHub(
                instance.session.screen,
                instance.screen,
                StreamSettings.from_config(config, instance.session.fps_limit),
                on_trouble=lambda reason: self._trouble(instance, reason),
                clock=self._clock,
                sleep=self._sleep,
            )
            self._set_state(instance, READY)
        except DeviceClaimed as exc:
            await self._fail(instance, self._copy.claimed(exc.claim.owner, exc.claim.pid), release=False)
        except (SimctlError, ConnectorError) as exc:
            await self._fail(instance, str(exc))
        except Exception as exc:
            logger.exception("the simulator %s could not be started", instance.udid)
            await self._fail(instance, f"The simulator could not be started: {exc}")

    def _set_state(self, instance: DeviceInstance, state: str, reason: str | None = None) -> None:
        instance.state, instance.reason, instance.since = state, reason, self._clock()
        self.publish_status(instance)

    def publish_status(self, instance: DeviceInstance) -> None:
        """Tell every screen watching how the device stands now: its state, and what it is busy with."""
        instance.events.publish({"type": "status", **instance.describe(self._clock())})

    def _trouble(self, instance: DeviceInstance, reason: str | None) -> None:
        if reason is not None and instance.state == READY:
            self._set_state(instance, STALLED, reason)
        elif reason is None and instance.state == STALLED:
            self._set_state(instance, READY)
        if reason is not None and self._session_gone(instance):
            self._restart(instance)

    @staticmethod
    def _session_gone(instance: DeviceInstance) -> bool:
        return instance.session is not None and not instance.session.alive

    def _restart(self, instance: DeviceInstance) -> None:
        """Attach the device's stopped connector again in the background, unless that is already under way."""
        if instance.recovery is None or instance.recovery.done():
            instance.recovery = asyncio.get_running_loop().create_task(self._recover(instance))

    async def _recover(self, instance: DeviceInstance) -> None:
        """A new session for a device whose connector stopped, its viewers kept; failed after the last attempt."""
        for attempt, delay in enumerate(RESTART_S, start=1):
            await self._sleep(delay)
            async with self._lock:
                if self._instances.get(instance.udid) is not instance or not instance.live:
                    return
                why = await self._attach_again(instance)
                if why is None:
                    logger.info("attached the simulator %s again", instance.udid)
                    return
                if attempt == len(RESTART_S):
                    await self._fail(
                        instance,
                        f"The simulator's connector stopped and could not be started again ({why}). "
                        "Start the simulator again.",
                    )

    async def _attach_again(self, instance: DeviceInstance) -> str | None:
        """Replace the device's stopped session, the frame hub moved onto the new one. Why it did not work, or None."""
        stopped, instance.session = instance.session, None
        if stopped is not None:
            await stopped.close()
        config = self._config.get(instance.owner)
        connector = self.availability.registry.get(instance.connector)
        if connector is None:
            return f"the {instance.connector} connector is no longer installed"
        try:
            session = await connector.attach(instance.udid, config)
        except ConnectorError as exc:
            return str(exc)
        try:
            screen = await session.screen.describe()
        except ConnectorError as exc:
            await session.close()
            return str(exc)
        instance.session, instance.screen = session, screen
        if instance.hub is not None:
            instance.hub.rebind(session.screen, screen)
        return None

    async def _fail(self, instance: DeviceInstance, reason: str, *, release: bool = True) -> None:
        """A device that cannot stream any more: let go of what it had; starting it again starts over."""
        session, instance.session = instance.session, None
        if instance.hub is not None:
            await instance.hub.close()
            instance.hub = None
        if session is not None:
            await session.close()
        if release:
            await self._claims.release(instance.udid)
        self._set_state(instance, FAILED, reason)

    async def stop(self, scope: Scope, *, shutdown_device: bool = False, restarting: bool = False) -> bool:
        """Let the scope go of its device, ending the device when no other scope uses it (or when asked to).

        `restarting` says it is about to be started again: its screens are told so, and come back by themselves.
        """
        async with self._lock:
            instance = self._instances.get(self._scopes.pop(scope.id, ""))
            if instance is None:
                return False
            instance.scopes.discard(scope.id)
            if instance.scopes and not shutdown_device:
                return True
            await self._end(instance, shutdown=shutdown_device, restarting=restarting)
            return True

    async def _end(
        self, instance: DeviceInstance, *, shutdown: bool, off: str | None = None, restarting: bool = False
    ) -> None:
        """End a device: its screens closed, its session let go, its claim released, and -- when asked -- the device
        shut down. `off` is why its scope has no simulator now, when that is why it ends."""
        self._instances.pop(instance.udid, None)
        for scope_id in [scope_id for scope_id, udid in self._scopes.items() if udid == instance.udid]:
            del self._scopes[scope_id]
        for task in (instance.task, instance.recovery):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._set_state(instance, STOPPED, off)
        if off:
            code, reason = CLOSE_FORBIDDEN, off
        elif restarting:
            code, reason = CLOSE_RESTARTING, RESTARTING_REASON
        else:
            code, reason = CLOSE_STOPPED, STOPPED_REASON
        for close in list(instance.sockets):
            with contextlib.suppress(Exception):
                await close(code, reason)
        instance.sockets.clear()
        if instance.hub is not None:
            await instance.hub.close()
        if instance.session is not None:
            await instance.session.close()
        await self._claims.release(instance.udid)
        if shutdown:
            try:
                await self._simctl_for(instance.developer_dir).shutdown(instance.udid)
            except SimctlError as exc:
                logger.warning("could not shut down the simulator %s: %s", instance.udid, exc)
        logger.info("ended the simulator %s (%s)", instance.name, "shut down" if shutdown else "left running")

    # -- viewers and tickets ------------------------------------------------------------------------------------

    def mint_ticket(self, instance: DeviceInstance, *, origin: str | None = None) -> str:
        return instance.tickets.mint(self._clock(), origin=origin)

    def consume_ticket(self, scope: Scope, ticket: str, *, origin: str | None = None) -> DeviceInstance | None:
        """The device this one-shot ticket opens a screen on, or None."""
        instance = self.instance(scope)
        if instance is None or not instance.tickets.consume(ticket, self._clock(), origin=origin):
            return None
        return instance

    def attach(self, instance: DeviceInstance, close: Closer) -> None:
        instance.sockets.add(close)
        instance.last_used = self._clock()

    def detach(self, instance: DeviceInstance, close: Closer) -> None:
        instance.sockets.discard(close)
        instance.last_used = self._clock()

    def touch(self, instance: DeviceInstance) -> None:
        instance.last_used = self._clock()

    def person_touched(self, instance: DeviceInstance) -> None:
        """A person put a finger (or a key) on the screen: an agent's next gesture waits for them."""
        instance.person_touch_at = instance.last_used = self._clock()

    def simctl(self, instance: DeviceInstance) -> Simctl:
        """simctl on the Xcode this device's scope uses."""
        return self._simctl_for(instance.developer_dir)

    # -- lifetime --------------------------------------------------------------------------------------------------

    async def reconcile(self, group: str | None = None) -> None:
        """Act on changed settings -- for one group, or every device -- before the change is answered: a scope that
        cannot have a simulator now has its device ended, one whose connector changed has it brought back on the new
        one, and new stream settings show at once."""
        async with self._lock:
            for instance in [i for i in self._instances.values() if group is None or i.group == group]:
                verdict = await self.availability.check(instance.owner)
                if verdict.reason:
                    await self._end(instance, shutdown=instance.may_shut_down, off=verdict.reason)
                elif verdict.connector is not None and verdict.connector.name != instance.connector:
                    await self._end(instance, shutdown=False, restarting=True)
                elif instance.hub is not None and instance.session is not None:
                    instance.hub.reconfigure(StreamSettings.from_config(verdict.config, instance.session.fps_limit))

    async def reap(self) -> list[str]:
        """End what was switched off or left idle, and attach again what lost its connector. Answers the UDIDs ended."""
        ended: list[str] = []
        async with self._lock:
            now = self._clock()
            for instance in list(self._instances.values()):
                verdict = await self.availability.check(instance.owner)
                if verdict.reason:
                    await self._end(instance, shutdown=instance.may_shut_down, off=verdict.reason)
                    ended.append(instance.udid)
                    continue
                if instance.live and self._session_gone(instance):
                    # Nobody watching sees no stream fail, so the reaper is what notices a connector that stopped.
                    self._restart(instance)
                    continue
                if instance.viewers or instance.busy or self.usage.in_use(instance):
                    instance.last_used = now
                    continue
                if now - instance.last_used >= verdict.config.idle_minutes * 60:
                    await self._end(instance, shutdown=verdict.config.shutdown_on_idle and instance.may_shut_down)
                    ended.append(instance.udid)
        return ended

    async def start_at_boot(self) -> int:
        """End whatever a previous run of this host left behind, connector by connector. Answers how much there was."""
        ended = 0
        for connector in self.availability.registry.connectors():
            try:
                ended += await connector.reap_orphans()
            except (ConnectorError, OSError) as exc:
                logger.warning("the %s connector could not clean up after a previous run: %s", connector.name, exc)
        return ended

    async def shutdown(self) -> None:
        """Let go of every device's session, leaving the devices themselves running for the next start."""
        async with self._lock:
            for instance in list(self._instances.values()):
                await self._end(instance, shutdown=False)
