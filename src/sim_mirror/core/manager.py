# SPDX-License-Identifier: Apache-2.0
"""Every scope's simulator, for one process.

A scope asks for its simulator (`ensure`); the answer comes at once, in state ``booting``, and the device is brought up
in the background -- claimed, booted if it was off, waited for until SpringBoard is up, attached to its connector,
described -- so a first boot that takes a minute never holds a request open. What happens next reaches every open screen
through the device's `EventBus`.

What keeps it contained:

* **one instance per device**, keyed by UDID, whichever scopes use it (``device.mode``), and one lock for every change
  to which devices run, so two scopes asking at once cannot bring the same device up twice; a device's session is
  attached and started over under that device's own lock (`DeviceInstance.session_lock`) instead, since starting
  WebDriverAgent can take minutes: the other devices, the reaper and ``stop`` go on meanwhile. The manager's lock is
  never waited for while a device's is held, nor a device's while the manager's is -- ending a device cancels its
  background attach, and a start-over a call is waiting for lets go of what it attached once it finds the device
  ended -- so the two can never wait on each other;
* **a claim per device** (`storage.claims`), so another process on the Mac -- another host, or a second copy of this
  one -- is refused a device this one is driving, and told who has it;
* **who may share a device** (`may_share`): a scope joins a device another scope is running, or picks it, only when
  the two may share -- a daemon serving several hosts keeps each host's devices its own, and hides the others'
  running devices from its picker;
* **``device.max_booted``**: asking for one more than that ends the least recently used device nobody watches, no agent
  holds and nothing is building on -- or refuses, saying why;
* **off means off**: `reconcile` runs when settings change, before the change is answered, and ends the devices of a
  scope that cannot have one now -- on a shared device, every scope is asked on its own: one switched off lets go of
  the device, its screens closed, while the others keep it; a connector switched since brings the device back on the
  new one; WebDriverAgent turned off starts a real device's session over, view only, its screens told; `reap` does
  all of it once a minute, and ends devices left idle for ``device.idle_minutes``;
* **what can be reached better is**: a session that says the device could now be reached better than it does -- a
  real device attached without its cable, plugged back in -- has a second session attached beside it, which takes its
  place once it is up (`improve`): a cable pulled for a moment does not leave the device untouchable until a person
  starts it over, and a device not yet reachable the moment its cable goes back in keeps the session it had;
* **a device shut down elsewhere is let go**: a simulator shut down from Simulator.app or simctl has its session ended
  -- its companion stopped, its claim released -- when the reaper next looks, as soon as a tool fails on it (`gone`),
  or as soon as its screen stream fails -- asked once then, and at most every `GONE_CHECK_S` while the trouble lasts
  -- so a watching viewer is told rather than left on a frozen frame, and the next ask brings it up again rather than
  answering with the dead one;
* **only what it booted or made is shut down**: ending a device a person had booted leaves it running, a device
  SimMirror made is shut down even after a restart forgot booting it, a real device is never shut down, whoever
  asks -- restarting one starts its session over (`reconnect`) -- and nothing is ever deleted here;
* **``auto`` keeps going**: a connector ``auto`` chose that cannot reach the device -- a native helper that starts but
  cannot send input -- gives way to the next that can be used (`Selection.candidates`), saying why; one that stops
  later is replaced the same way, by a connector that can do everything it could;
* **a real device is given back when its session ends**: the reaper attaches a simulator's stopped connector again,
  but leaves a real device's ended session -- WebDriverAgent killed from the device or by ``devicectl`` -- as it is,
  since that is a person taking the phone back; the next `ensure`, a tool call or a viewer opened, starts it over and
  waits for it -- and a second call meanwhile waits for that one rather than starting another. Every such decision is
  logged with why.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import (
    Capability,
    Connector,
    ConnectorError,
    ConnectorReport,
    DeviceSession,
    ReportsTrouble,
)
from sim_mirror.connectors.registry import Selection
from sim_mirror.core.availability import Availability, Verdict
from sim_mirror.core.backends import BOOT_TIMEOUT_S as BOOT_TIMEOUT_S
from sim_mirror.core.backends import DeviceBackend, SimulatorBackend
from sim_mirror.core.control import DeviceControl
from sim_mirror.core.device_changes import ChangeJournal, Changes, Left, undo
from sim_mirror.core.devices import DeviceDirectory, NoDevice
from sim_mirror.core.frames import FrameHub, StreamSettings
from sim_mirror.core.instance import FAILED, READY, STALLED, STOPPED, Closer, DeviceInstance
from sim_mirror.core.signing import SigningTeams
from sim_mirror.core.status import scope_status
from sim_mirror.host_copy import HostCopy
from sim_mirror.platform.errors import DeviceControlError
from sim_mirror.platform.identifiers import kind_of
from sim_mirror.platform.keyboard import KeyboardCheck, mac_keyboard_is_us
from sim_mirror.platform.simctl import Simctl
from sim_mirror.protocol import (
    APPEARANCES,
    CLOSE_FORBIDDEN,
    CLOSE_RESTARTING,
    CLOSE_STOPPED,
    AppHierarchy,
    DeviceChoice,
    DeviceKind,
    ScopeStatus,
)
from sim_mirror.scope import Scope
from sim_mirror.seams import ConfigSource, HeldDevice, UsageProbe
from sim_mirror.storage.claims import Claim, Claims, DeviceClaimed

logger = logging.getLogger(__name__)

#: Why a device's session was not started over: it was ended while it was being attached.
LET_GO_MEANWHILE = "it was let go while its session was being started again"
#: How long a device whose connector stopped waits before each attempt to attach it again; then it has failed.
RESTART_S = (1.0, 5.0, 30.0)
STOPPED_REASON = "the simulator stopped"
SHUT_DOWN_REASON = "the simulator was shut down"
#: The states in which a device has been up, so that its device not being booted means it went away.
UP = frozenset({READY, STALLED})
RESTARTING_REASON = "the simulator is restarting"
#: How soon a device whose screen keeps failing is asked again whether it was shut down outside SimMirror.
GONE_CHECK_S = 10.0
#: What a real device whose session ended while nothing asked for it says, until the next call starts it over.
SESSION_ENDED_NOTE = (
    "Its session ended -- WebDriverAgent stopped, or its cable was pulled; the next call starts it again."
)


#: A connector a device may be attached with, and what it reported; None when it was not probed this time.
Choice = tuple[Connector, ConnectorReport | None]


class SimulatorUnavailable(Exception):
    """Why a simulator could not be had, with the HTTP status it means."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


def _anyone(scope: Scope, other: Scope) -> bool:
    return True


class NoUsage:
    """A `UsageProbe` for a host with no agents of its own to ask about."""

    def in_use(self, device: HeldDevice) -> bool:
        return False


@dataclass(frozen=True)
class _Holding:
    """One scope's hold on a device, as a usage probe is asked about it."""

    udid: str
    group: str
    scopes: tuple[str, ...]


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
        keyboard_is_us: KeyboardCheck = mac_keyboard_is_us,
        may_share: Callable[[Scope, Scope], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        backends: Mapping[DeviceKind, DeviceBackend] | None = None,
        journal: ChangeJournal | None = None,
        signing: SigningTeams | None = None,
        default_kind: Callable[[], DeviceKind] = lambda: "simulator",
    ) -> None:
        self.availability = availability
        self.directory = directory
        #: Each kind of device, and how it is listed, brought up and put away: simulators unless told otherwise.
        self.backends: Mapping[DeviceKind, DeviceBackend] = backends or {
            "simulator": SimulatorBackend(simctl_for, directory)
        }
        #: Whether an agent holds a device; a host points it at its own sessions.
        self.usage: UsageProbe = usage or NoUsage()
        #: Whether the keys SimMirror presses type what they are for: asked each time text is typed.
        self.keyboard_is_us = keyboard_is_us
        #: Whether two scopes may use one device; every two may, unless a host says otherwise.
        self._may_share = may_share or _anyone
        self._config = config
        self._claims = claims
        self._copy = copy or HostCopy()
        self._clock = clock
        self._sleep = sleep
        self._instances: dict[str, DeviceInstance] = {}
        self._scopes: dict[str, str] = {}
        self._lock = asyncio.Lock()
        #: Each device's own lock, by UDID, given to every instance of it (`DeviceInstance.session_lock`).
        self._session_locks: dict[str, asyncio.Lock] = {}
        #: What each device still has changed, written down so a start after a crash can put it back.
        self._journal = journal or ChangeJournal()
        #: Which team signs for each scope's real device; its connector is given the scope's settings with it.
        self._signing = signing
        #: The kind of device a scope that remembers none gets: a simulator, unless this Mac cannot run one.
        self._default_kind = default_kind
        #: Told when a device ends, so what was kept about it goes with it.
        self.on_end: list[Callable[[DeviceInstance], None]] = []

    # -- what there is --------------------------------------------------------------------------------------------

    def instance(self, scope: Scope) -> DeviceInstance | None:
        return self._instances.get(self._scopes.get(scope.id, ""))

    def is_current(self, instance: DeviceInstance) -> bool:
        """Whether its device still runs as this instance, rather than it having been ended since."""
        return self._instances.get(instance.udid) is instance

    def instances(self) -> list[DeviceInstance]:
        return list(self._instances.values())

    def now(self) -> float:
        return self._clock()

    def kind(self, scope: Scope) -> DeviceKind:
        """The kind of the scope's device: the one it runs, else the one it remembers, else a simulator -- or an
        Android device, on a Mac with the Android SDK and no Xcode."""
        current = self.instance(scope)
        if current is not None:
            return current.kind
        return kind_of(self._remembered(scope, self._config.get(scope))) or self._default_kind()

    def _remembered(self, scope: Scope, config: SimConfig) -> str | None:
        return self.directory.memory.assigned(scope, config.device_mode == "shared")

    def backend(self, kind: DeviceKind) -> DeviceBackend:
        """The backend of a kind of device. Refuses a kind this host does not drive."""
        found = self.backends.get(kind)
        if found is None:
            raise SimulatorUnavailable(self._copy.kind_unavailable(kind))
        return found

    async def unavailable(self, scope: Scope) -> str | None:
        """Why this scope cannot have its device right now, or None when it can."""
        return (await self.availability.check(scope, self.kind(scope))).reason

    async def _available(self, scope: Scope) -> SimConfig:
        """The scope's settings -- or a refusal while it cannot have a simulator: off means nothing asks xcrun."""
        verdict = await self.availability.check(scope)
        if verdict.reason:
            raise SimulatorUnavailable(verdict.reason)
        return verdict.config

    async def status(self, scope: Scope) -> ScopeStatus:
        verdict = await self.availability.check(scope, self.kind(scope))
        return scope_status(verdict, self.instance(scope), self._clock())

    async def devices(self, scope: Scope) -> list[DeviceChoice]:
        """The devices the scope could use -- this Mac's iOS simulators, then the real devices connected to it -- for a
        picker. Refused while it can have none; a kind that cannot be listed is left out, unless none can be."""
        config = await self._available(scope)
        created = self.directory.memory.created(scope)
        listed: list[DeviceChoice] = []
        failures: list[DeviceControlError] = []
        for backend in self.backends.values():
            try:
                found = await backend.choices(config, created)
            except DeviceControlError as exc:
                logger.warning("could not list the %s devices: %s", backend.kind, exc)
                failures.append(exc)
                continue
            listed += [choice for choice in found if self._shareable(scope, self._instances.get(choice["udid"]))]
        if failures and len(failures) == len(self.backends):
            raise SimulatorUnavailable(str(failures[0]), 502) from failures[0]
        return listed

    async def choose(self, scope: Scope, udid: str, *, restarting: bool = False) -> None:
        """Use the device picked for this scope from now on, letting go of the one it had. Refused while it can have
        none. `restarting` tells the screens on the old one to come back by themselves, on the new one."""
        config = await self._available(scope)
        kind = kind_of(udid)
        if kind is None:
            raise SimulatorUnavailable(f"not a device id: {udid!r}", 400)
        try:
            found = await self.backend(kind).lookup(udid, config)
        except DeviceControlError as exc:
            raise SimulatorUnavailable(str(exc), 400) from exc
        if found is None:
            raise SimulatorUnavailable(self._copy.no_such_device(kind), 404)
        if not found.get("usable", True):
            last = self.last_error(udid)
            raise SimulatorUnavailable(self._copy.not_usable_after(found["name"], found.get("detail"), last), 409)
        if not self._shareable(scope, self._instances.get(udid)):
            raise SimulatorUnavailable(self._copy.in_use_elsewhere_of_kind(kind), 409)
        await self.stop(scope, restarting=restarting)
        self.directory.memory.choose(scope, config.device_mode == "shared", udid)

    def last_error(self, udid: str) -> str | None:
        """What last kept a device from being driven -- WebDriverAgent on a real iPhone -- with when, as the first of
        the connectors for its kind that remembers says; None when none does."""
        kind = kind_of(udid)
        registry = self.availability.registry
        for name in registry.names(kind) if kind is not None else ():
            connector: object = registry.get(name)
            if isinstance(connector, ReportsTrouble) and (said := connector.last_error(udid)):
                return said
        return None

    # -- a device's life -------------------------------------------------------------------------------------------

    async def ensure(self, scope: Scope) -> DeviceInstance:
        """The scope's device: running, or being brought up. Refuses with a reason and a status.

        The kind of the device comes first -- the one the scope runs, else the one it remembers -- since that decides
        which connectors can reach it."""
        kind = self.kind(scope)
        verdict = await self.availability.check(scope, kind)
        if verdict.reason or verdict.selection is None or verdict.connector is None:
            raise SimulatorUnavailable(verdict.reason or "No connector can reach a simulator here.")
        backend = self.backend(kind)
        async with self._lock:
            instance, running = await self._held(scope, verdict.config, verdict.selection, verdict.connector, backend)
        # Once the manager's lock is let go: starting a session over waits on this device alone.
        return await self._revived(instance) if running else instance

    async def _held(
        self, scope: Scope, config: SimConfig, selection: Selection, connector: Connector, backend: DeviceBackend
    ) -> tuple[DeviceInstance, bool]:
        """The scope's device, and whether it was running already: the one it has, else the one it joins, else one
        made and being brought up. The manager's lock is held."""
        now = self._clock()
        current = self.instance(scope)
        if current is not None and current.live:
            current.last_used = now
            return current, True
        # A device an instance that did not come up had booted is still SimMirror's to shut down.
        booted: set[str] = set()
        # The other scopes on it, kept on the instance that replaces it while an agent still holds them.
        holders: dict[str, list[Scope]] = {}
        if current is not None:
            if current.booted_by_us:
                booted.add(current.udid)
            holders[current.udid] = self._leased(current, scope)
            await self._end(current, shutdown=False)
        try:
            ref = await backend.resolve(scope, self._remembered(scope, config), config)
        except NoDevice as exc:
            raise SimulatorUnavailable(str(exc), exc.status) from exc
        except DeviceControlError as exc:
            raise SimulatorUnavailable(str(exc), 502) from exc
        running = self._instances.get(ref.udid)
        if not self._shareable(scope, running):
            raise SimulatorUnavailable(self._copy.in_use_elsewhere_of_kind(ref.kind), 409)
        if running is not None and running.live:
            running.scopes.add(scope.id)
            running.members[scope.id] = scope
            running.last_used = now
            self._scopes[scope.id] = ref.udid
            return running, True
        if running is not None:
            if running.booted_by_us:
                booted.add(running.udid)
            holders[running.udid] = self._leased(running, scope)
            await self._end(running, shutdown=False)
        if backend.runs_here(ref):
            await self._make_room(config.max_booted)
        report = selection.report
        instance = DeviceInstance(
            udid=ref.udid,
            name=ref.name,
            runtime=ref.runtime,
            owner=scope,
            developer_dir=backend.developer_dir(config),
            scopes={scope.id},
            created=ref.created,
            booted_by_us=ref.udid in booted,
            connector=connector.name,
            chosen=connector.name,
            capabilities=report.capabilities if report is not None else frozenset(),
            fallback_reason=selection.fallback_reason,
            since=now,
            last_used=now,
            kind=ref.kind,
            connection=ref.connection,
            session_lock=self._session_locks.setdefault(ref.udid, asyncio.Lock()),
        )
        self._instances[ref.udid] = instance
        self._scopes[scope.id] = ref.udid
        for other in holders.get(ref.udid, ()):
            instance.scopes.add(other.id)
            instance.members[other.id] = other
            self._scopes[other.id] = ref.udid
        instance.task = asyncio.get_running_loop().create_task(
            self._bring_up(instance, backend, config, selection.choices)
        )
        return instance, False

    async def _revived(self, instance: DeviceInstance) -> DeviceInstance:
        """A running device asked for now, its session started over when it had ended -- one the reaper left ended, or
        has not noticed yet -- so the caller waits for it rather than being told the device cannot be reached.

        Under the device's own lock, not the manager's, which is not held: WebDriverAgent can take minutes to start
        (``real_devices.wda.startup_timeout``), and every other device, the reaper and ``stop`` go on meanwhile. A
        second call for the device -- which has no session while it is attached again -- waits for that, and finds its
        session up rather than starting it again; a device ended or replaced meanwhile is not started over, and the
        caller is answered it as it is now. One still being brought up is answered at once, as ever."""
        if not self._session_gone(instance) and not (instance.session is None and instance.state in UP):
            return instance
        async with instance.session_lock:
            if self.is_current(instance) and instance.live and self._session_gone(instance):
                logger.info("the %s %s's session had ended; starting it again for a call", instance.kind, instance.udid)
                await self._start_over(instance)
        return instance

    def _leased(self, instance: DeviceInstance, scope: Scope) -> list[Scope]:
        """The other scopes on a device about to be ended and started again for `scope` that an agent still holds,
        and that may share with it: they keep the device, rather than being dropped from it unasked."""
        return [
            member
            for member in instance.members.values()
            if member.id != scope.id and self._may_share(scope, member)
            if self.usage.in_use(_Holding(instance.udid, instance.group, (member.id,)))
        ]

    def _shareable(self, scope: Scope, instance: DeviceInstance | None) -> bool:
        """Whether a scope may use a device: one nobody runs, or one run only by scopes it may share with."""
        if instance is None or not instance.live:
            return True
        return all(self._may_share(scope, member) for member in instance.members.values())

    async def _make_room(self, limit: int) -> None:
        """End the least recently used idle simulator while as many run as ``device.max_booted`` allows."""
        while True:
            live = [instance for instance in self._instances.values() if instance.live and self.runs_here(instance)]
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
        self, instance: DeviceInstance, backend: DeviceBackend, config: SimConfig, choices: Sequence[Choice]
    ) -> None:
        # Under the device's own lock: an instance of it ended while its session was being started over is attached
        # by nothing else until that is done.
        async with instance.session_lock:
            await self._brought_up(instance, backend, config, choices)

    async def _brought_up(
        self, instance: DeviceInstance, backend: DeviceBackend, config: SimConfig, choices: Sequence[Choice]
    ) -> None:
        try:
            await self._claims.acquire(instance.udid)
            await backend.prepare(instance, config)
            instance.session = await self._attach(instance, config, choices)
            if config.demo_status_bar == "demo" and instance.session.can(Capability.STATUS_BAR):
                await self._demo_status_bar(instance, backend)
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
            await self._fail(
                instance, self._copy.claimed_of_kind(exc.claim.owner, exc.claim.pid, instance.kind), release=False
            )
        except (DeviceControlError, ConnectorError, NoDevice) as exc:
            await self._fail(instance, str(exc))
        except Exception as exc:
            logger.exception("the %s %s could not be started", instance.kind, instance.udid)
            await self._fail(instance, f"The {self.noun(instance)} could not be started: {exc}")

    @staticmethod
    async def _demo_status_bar(instance: DeviceInstance, backend: DeviceBackend) -> None:
        """Give the device the demo status bar ``device.status_bar`` asks for; a device that refuses keeps its own."""
        try:
            await backend.control(instance.developer_dir).demo_status_bar(instance.udid)
        except DeviceControlError as exc:
            logger.warning("could not give %s a demo status bar: %s", instance.udid, exc)
            return
        instance.demo_status_bar = True

    async def _put_back(self, instance: DeviceInstance, backend: DeviceBackend) -> None:
        """Take away the demo status bar ``device.status_bar`` gave, and put back what else SimMirror changed on the
        device when ``device.restore_changes`` says to."""
        if instance.demo_status_bar:
            instance.demo_status_bar = False
            try:
                await backend.control(instance.developer_dir).clear_status_bar(instance.udid)
            except DeviceControlError as exc:
                logger.warning("could not give %s its own status bar back: %s", instance.udid, exc)
        if self._restores(instance):
            # Its ledger, met with the journal: what an earlier session could not put back is put back now too.
            self.changes(instance)
            await instance.changes.restore(backend.control(instance.developer_dir), instance.udid)

    def _restores(self, instance: DeviceInstance) -> bool:
        """Whether what SimMirror changes on this device is put back when it is let go (``device.restore_changes``)."""
        restoring = self._config.get(instance.owner).restore_changes
        return restoring == "all" or (restoring == "real_devices" and not self.runs_here(instance))

    def changes(self, instance: DeviceInstance) -> Changes:
        """Changes to how the device looks and where it is, remembered to be put back when its settings say."""
        return instance.changes.on(
            self.control(instance),
            instance.udid,
            remember=self._restores(instance),
            journal=self._journal,
            developer_dir=instance.developer_dir,
            on_appearance=lambda mode: self.appearance_is(instance, mode),
        )

    def appearance_is(self, instance: DeviceInstance, mode: str | None) -> None:
        """The device is light or dark now, as SimMirror set or read it: every screen watching is told."""
        appearance = next((known for known in APPEARANCES if known == mode), None)
        if appearance is None or instance.appearance == appearance:
            return
        instance.appearance = appearance
        self.publish_status(instance)

    async def read_appearance(self, instance: DeviceInstance) -> None:
        """Learn whether the device is light or dark, once, when its connector can change that -- so a viewer's
        appearance button says what the device is before anyone has changed it."""
        session = instance.session
        if instance.appearance is not None or session is None or not session.can(Capability.APPEARANCE):
            return
        try:
            state = await self.control(instance).display(instance.udid)
        except DeviceControlError as exc:
            logger.debug("could not read whether %s is light or dark: %s", instance.udid, exc)
            return
        self.appearance_is(instance, state.appearance)

    def _set_state(self, instance: DeviceInstance, state: str, reason: str | None = None) -> None:
        instance.state, instance.reason, instance.since = state, reason, self._clock()
        self.publish_status(instance)

    def publish_status(self, instance: DeviceInstance) -> None:
        """Tell every screen watching how the device stands now: its state, and what it is busy with."""
        instance.events.publish({"type": "status", **instance.describe(self._clock())})

    def share_app(self, udid: str, app: AppHierarchy | None) -> None:
        """An app in front of a device shares its view hierarchy now, or none does: every screen watching is told."""
        instance = self._instances.get(udid)
        if instance is None or instance.app_hierarchy == app:
            return
        instance.app_hierarchy = app
        self.publish_status(instance)

    def _trouble(self, instance: DeviceInstance, reason: str | None) -> None:
        if reason is not None and instance.state == READY:
            self._set_state(instance, STALLED, reason)
        elif reason is None and instance.state == STALLED:
            self._set_state(instance, READY)
        if reason is not None and self._session_gone(instance):
            self._restart(instance, f"its screen failed ({reason}) while it was watched")
        if reason is not None:
            self._check_gone_soon(instance)

    def _check_gone_soon(self, instance: DeviceInstance) -> None:
        """Ask in the background whether a device whose screen failed was shut down outside SimMirror, ending it if
        so: once when the trouble starts, then no more than every `GONE_CHECK_S` while it lasts."""
        pending = instance.gone_check
        now = self._clock()
        if (pending is not None and not pending.done()) or now - instance.gone_checked_at < GONE_CHECK_S:
            return
        instance.gone_checked_at = now
        instance.gone_check = asyncio.get_running_loop().create_task(self._end_if_gone(instance))

    async def _end_if_gone(self, instance: DeviceInstance) -> bool:
        async with self._lock:
            return await self._ended_if_gone(instance)

    @staticmethod
    def _session_gone(instance: DeviceInstance) -> bool:
        return instance.session is not None and not instance.session.alive

    def _restart(self, instance: DeviceInstance, why: str) -> None:
        """Attach the device's stopped connector again in the background, unless that is already under way; `why` is
        logged."""
        if instance.recovery is None or instance.recovery.done():
            logger.info("attaching the %s %s again: %s", instance.kind, instance.udid, why)
            instance.recovery = asyncio.get_running_loop().create_task(self._recover(instance, instance.session))

    async def _recover(self, instance: DeviceInstance, session: DeviceSession | None) -> None:
        """A new session for a device whose connector stopped, its viewers kept; failed after the last attempt.

        Under the device's own lock: one ended meanwhile, or whose `session` was started over by something else -- a
        call, a reconnect -- is left as it is."""
        for attempt, delay in enumerate(RESTART_S, start=1):
            await self._sleep(delay)
            async with instance.session_lock:
                if not self.is_current(instance) or not instance.live or instance.session is not session:
                    return
                why = await self._attach_again(instance)
                if not self.is_current(instance):
                    return
                if why is None:
                    logger.info("attached the %s %s again", instance.kind, instance.udid)
                    self._told_again(instance)
                    return
                session = instance.session
                if attempt == len(RESTART_S):
                    await self._fail(
                        instance,
                        f"The {self.noun(instance)}'s connector stopped and could not be started again ({why}). "
                        f"Start the {self.noun(instance)} again.",
                    )

    def noun(self, instance: DeviceInstance) -> str:
        """How a reason names a device: a simulator, an emulator running here, or a real device."""
        if instance.kind == "simulator":
            return "simulator"
        return "emulator" if self.runs_here(instance) else "device"

    async def _attach_again(self, instance: DeviceInstance) -> str | None:
        """Replace the device's stopped session, the frame hub moved onto the new one. Why it did not work, or None."""
        stopped, instance.session = instance.session, None
        if stopped is not None:
            await stopped.close()
        if instance.fallback_reason == SESSION_ENDED_NOTE:
            instance.fallback_reason = None
        config = self._config.get(instance.owner)
        connector = self.availability.registry.get(instance.connector)
        if connector is None:
            return f"the {instance.connector} connector is no longer installed"
        choices: list[Choice] = [(connector, None)]
        if config.connector == "auto" or instance.kind != "simulator":
            selection = await self.availability.registry.select(config, instance.kind)
            choices += [(other, report) for other, report in selection.choices if other is not connector]
        try:
            session = await self._attach(instance, config, choices)
        except ConnectorError as exc:
            return str(exc)
        try:
            screen = await session.screen.describe()
        except ConnectorError as exc:
            await session.close()
            return str(exc)
        if not self.is_current(instance):
            # Ended while it was attached -- stopped, let go -- with nothing left to give the session to.
            await session.close()
            return LET_GO_MEANWHILE
        instance.session, instance.screen = session, screen
        if instance.hub is not None:
            instance.hub.rebind(session.screen, screen)
        return None

    async def _attach(self, instance: DeviceInstance, config: SimConfig, choices: Sequence[Choice]) -> DeviceSession:
        """A session from the first of `choices` that reaches the device, the instance naming the connector used.

        A connector after the first is used only when the ones before it could not attach, and only when it can do
        everything the device was offered -- a device is never quietly left unable to take a touch it could take a
        moment ago -- and the instance says why. Raises the first refusal when none attaches.
        """
        refusals: list[ConnectorError] = []
        for connector, report in choices:
            if refusals and (report is None or not instance.capabilities <= report.capabilities):
                continue
            try:
                session = await connector.attach(instance.udid, await self._signed(instance, config))
            except ConnectorError as exc:
                logger.warning("the %s connector could not reach %s: %s", connector.name, instance.udid, exc)
                refusals.append(exc)
                continue
            # The note of the session it had went with that session: a cause put right since -- a cable back in --
            # leaves no note behind. Why a lesser connector drives it stays.
            had = instance.session
            noted = had.note if had is not None else instance.session_note
            instance.session_note = session.note
            kept = None if instance.fallback_reason == noted else instance.fallback_reason
            if connector.name != instance.connector:
                instance.connector = connector.name
                said = (kept, *(str(refusal) for refusal in refusals))
                instance.fallback_reason = kept = " ".join(filter(None, said)) or None
            # What the session can do is the truth: a real device's depends on what is plugged in and set up now.
            told = (instance.capabilities, instance.fallback_reason)
            instance.capabilities = session.capabilities
            instance.fallback_reason = session.note or kept
            if (instance.capabilities, instance.fallback_reason) != told:
                await self._hello_again(instance)
            return session
        raise refusals[0] if refusals else ConnectorError("No connector can reach this simulator.")

    async def _signed(self, instance: DeviceInstance, config: SimConfig) -> SimConfig:
        """The settings a real device is attached with: ``real_devices.team_id`` as the team its owner's scope signs
        with (`core.signing`)."""
        if instance.kind != "physical" or self._signing is None:
            return config
        return await self._signing.applied(instance.owner, config)

    def reattach(self, udid: str) -> bool:
        """Attach a running device's connector again in the background, its viewers kept -- after what it can reach
        has changed, such as WebDriverAgent set up for it. False when the device is not running."""
        instance = self._instances.get(udid)
        if instance is None or not instance.live or instance.session is None:
            return False
        self._restart(instance, "what it can be reached with changed")
        return True

    async def reconnect(self, scope: Scope) -> bool:
        """Start the session on the scope's running device over -- its connector closed and attached again, its
        screens kept watching, what was changed on it left as it is -- without asking anything of the device itself.

        This is what restarting a real device means here: SimMirror never restarts, powers or shuts one down. False
        when nothing runs for the scope, so there is no session to start over. Raises `SimulatorUnavailable` when the
        device cannot be reached again -- unplugged, locked -- once it has failed saying why; asking for it again then
        starts over, and is refused while it is still not there. Under the device's own lock (`_revived`): a device
        ended meanwhile answers False too.
        """
        async with self._lock:
            instance = self.instance(scope)
        if instance is None:
            return False
        async with instance.session_lock:
            if not self.is_current(instance) or not instance.live or instance.session is None:
                return False
            why = await self._start_over(instance)
        if why is None:
            return True
        if not self.is_current(instance):
            return False
        raise SimulatorUnavailable(why)

    async def _start_over(self, instance: DeviceInstance) -> str | None:
        """Close the device's session and attach it again, under the device's own lock: why it could not be, once it
        has failed saying so, or None. Screens told what it could do hear it again when that changed. A device ended
        while it was attached is neither failed nor told: its end said all there is."""
        # Done here and now: a reattach the connector's own trouble had queued would only do it again.
        if instance.recovery is not None and not instance.recovery.done():
            instance.recovery.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await instance.recovery
        why = await self._attach_again(instance)
        if not self.is_current(instance):
            # Failing it would release a claim a new instance of the device may hold by now.
            return why
        if why is not None:
            await self._fail(instance, why)
        else:
            self._told_again(instance)
        return why

    def _told_again(self, instance: DeviceInstance) -> None:
        """Tell screens again how a device attached again stands, when it stood ready all along -- nothing else says
        so then, and what it can do came with its new session: its buttons, once scrcpy or WebDriverAgent is there.
        A viewer that reconnected while it had no session was told it had none. A device recovering from trouble is
        told when it is ready again."""
        if instance.state == READY:
            self.publish_status(instance)

    @staticmethod
    async def _hello_again(instance: DeviceInstance) -> None:
        """Screens told what the device could do before its session said otherwise hear it again: their sockets close
        as restarting, and each viewer reconnects to a hello that says what is so."""
        for close in list(instance.sockets):
            with contextlib.suppress(Exception):
                await close(CLOSE_RESTARTING, RESTARTING_REASON)

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
            instance.members.pop(scope.id, None)
            if instance.scopes and not shutdown_device:
                self._hand_on(instance)
                return True
            await self._end(instance, shutdown=shutdown_device, restarting=restarting)
            return True

    async def let_go(self, udid: str) -> list[Scope] | None:
        """End a device's session for every scope using it -- ``sim-mirror stop --device`` -- leaving the device itself
        running. Answers the scopes that used it, or None when SimMirror does not run it."""
        async with self._lock:
            instance = self._instances.get(udid)
            if instance is None:
                return None
            held = list(instance.members.values())
            logger.info("letting go of the %s %s for every scope using it: %s", instance.kind, udid,
                        ", ".join(scope.id for scope in held) or "none")  # fmt: skip
            await self._end(instance, shutdown=False)
            return held

    async def gone(self, scope: Scope) -> bool:
        """Whether the scope's device went away outside SimMirror -- its simulator shut down -- ending its session when
        it did, so the next `ensure` brings it up again. One listing of the device's own tool; a device not yet up,
        busy, or of a kind that is never shut down is not asked about."""
        async with self._lock:
            instance = self.instance(scope)
            return instance is not None and await self._ended_if_gone(instance)

    async def _ended_if_gone(self, instance: DeviceInstance) -> bool:
        """Whether this running device went away, ending it when it did; the lock is held."""
        if not self.is_current(instance) or instance.state not in UP or instance.busy:
            return False
        if not await self.backend(instance.kind).gone([instance]):
            return False
        await self._end_gone(instance)
        return True

    async def _gone(self) -> set[str]:
        """The UDIDs of the running devices that went away outside SimMirror, each kind's asked about at once."""
        found: set[str] = set()
        for kind, backend in self.backends.items():
            up = [i for i in self._instances.values() if i.kind == kind and i.state in UP and not i.busy]
            if up:
                found.update(instance.udid for instance in await backend.gone(up))
        return found

    async def _end_gone(self, instance: DeviceInstance) -> None:
        logger.info("the %s %s was shut down outside SimMirror; ending its session", instance.kind, instance.name)
        await self._end(instance, shutdown=False, stopped=SHUT_DOWN_REASON)

    async def _end(
        self,
        instance: DeviceInstance,
        *,
        shutdown: bool,
        off: str | None = None,
        restarting: bool = False,
        stopped: str | None = None,
    ) -> None:
        """End a device: its screens closed, its session let go, its claim released, and -- when asked -- the device
        shut down. `off` is why its scope has no simulator now, when that is why it ends; `stopped` is why it ends when
        nobody asked it to -- the device went away."""
        self._instances.pop(instance.udid, None)
        for scope_id in [scope_id for scope_id, udid in self._scopes.items() if udid == instance.udid]:
            del self._scopes[scope_id]
        for task in (instance.task, instance.recovery):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        if instance.gone_check is not None and instance.gone_check is not asyncio.current_task():
            # Waiting for the lock this end holds: there is nothing left for it to ask about.
            instance.gone_check.cancel()
        self._set_state(instance, STOPPED, off or stopped)
        if off:
            code, reason = CLOSE_FORBIDDEN, off
        elif restarting:
            code, reason = CLOSE_RESTARTING, RESTARTING_REASON
        else:
            code, reason = CLOSE_STOPPED, stopped or STOPPED_REASON
        for close in list(instance.sockets):
            with contextlib.suppress(Exception):
                await close(code, reason)
        instance.sockets.clear()
        if instance.recording is not None:
            # Before the screen's sources close: what was recorded is kept, rendered in the background.
            await instance.recording.end()
        backend = self.backends[instance.kind]
        # Before the session closes: a real device's cable screen letting go drops its connection for a moment, and
        # what SimMirror changed on it is put back while it can still be reached.
        await self._put_back(instance, backend)
        if instance.hub is not None:
            await instance.hub.close()
        if instance.session is not None:
            await instance.session.close()
        await self._claims.release(instance.udid)
        # Only a device running on this Mac is ever asked to shut down: a real one is let go of and left as it is,
        # whoever asks.
        shut = shutdown and backend.runs_here(instance)
        await backend.release(instance, shutdown=shut)
        for listener in self.on_end:
            listener(instance)
        logger.info("ended the %s %s (%s)", instance.kind, instance.name, "shut down" if shut else "left running")

    # -- viewers and tickets ------------------------------------------------------------------------------------

    def mint_ticket(self, instance: DeviceInstance, *, origin: str | None = None) -> str:
        return instance.tickets.mint(self._clock(), origin=origin)

    def consume_ticket(self, scope: Scope, ticket: str, *, origin: str | None = None) -> DeviceInstance | None:
        """The device this one-shot ticket opens a screen on, or None."""
        instance = self.instance(scope)
        if instance is None or not instance.tickets.consume(ticket, self._clock(), origin=origin):
            return None
        return instance

    def attach(self, instance: DeviceInstance, close: Closer, scope_id: str | None = None) -> None:
        """A screen socket opened on the device by a scope -- its owner when not said -- so it can be closed alone."""
        instance.sockets[close] = scope_id or instance.owner.id
        instance.last_used = self._clock()

    def detach(self, instance: DeviceInstance, close: Closer) -> None:
        instance.sockets.pop(close, None)
        instance.last_used = self._clock()

    def touch(self, instance: DeviceInstance) -> None:
        instance.last_used = self._clock()

    def person_touched(self, instance: DeviceInstance) -> None:
        """A person put a finger (or a key) on the screen: an agent's next gesture waits for them."""
        instance.person_touch_at = instance.last_used = self._clock()

    async def holder(self, udid: str) -> Claim | None:
        """The live claim another process on this Mac has on a device, or None."""
        return await self._claims.holder(udid)

    def control(self, instance: DeviceInstance) -> DeviceControl:
        """The device's own tool -- simctl or devicectl -- on the Xcode its scope uses."""
        return self.backend(instance.kind).control(instance.developer_dir)

    def runs_here(self, instance: DeviceInstance) -> bool:
        """Whether the device runs on this Mac and may be shut down, rather than being a real one connected to it."""
        return self.backend(instance.kind).runs_here(instance)

    # -- lifetime --------------------------------------------------------------------------------------------------

    @staticmethod
    def _hand_on(instance: DeviceInstance) -> None:
        """Give the device to a scope still using it when its owner no longer does."""
        if instance.owner.id not in instance.members and instance.members:
            instance.owner = next(iter(instance.members.values()))

    def _why_off(self, instance: DeviceInstance, verdict: Verdict) -> str | None:
        """Why a scope may not keep this device now: it may have no device at all, or its settings no longer allow
        its kind -- ``real_devices.enabled`` off for a real device -- which ends the session, and leaves the device as
        it is."""
        if verdict.reason:
            return verdict.reason
        return self.backend(instance.kind).withheld(instance, verdict.config)

    @staticmethod
    def _touch_withdrawn(instance: DeviceInstance, verdict: Verdict) -> bool:
        """Whether a real device is touched through WebDriverAgent that ``real_devices.wda.enabled`` now turns off for
        its owner: its session is started over at once, view only, rather than at its next attach."""
        session = instance.session
        driven = session is not None and session.input is not None
        return instance.kind == "physical" and driven and not verdict.config.wda_enabled

    async def _govern(self, instance: DeviceInstance) -> tuple[Verdict, str | None]:
        """Ask every scope using the device again. A scope that cannot have a simulator now lets go of it -- its
        screens closed with why -- and the owner is handed on when it was one of them. Answers the owner's verdict,
        and why the device ends when no scope may have it any more."""
        verdicts = {
            scope_id: await self.availability.check(member, instance.kind)
            for scope_id, member in list(instance.members.items())
        }
        off = {scope_id: why for scope_id, verdict in verdicts.items() if (why := self._why_off(instance, verdict))}
        if len(off) == len(verdicts):
            return verdicts[instance.owner.id], off[instance.owner.id]
        for scope_id, reason in off.items():
            for close, opened_by in list(instance.sockets.items()):
                if opened_by == scope_id:
                    instance.sockets.pop(close, None)
                    with contextlib.suppress(Exception):
                        await close(CLOSE_FORBIDDEN, reason or "")
            instance.members.pop(scope_id, None)
            instance.scopes.discard(scope_id)
            self._scopes.pop(scope_id, None)
        self._hand_on(instance)
        return verdicts[instance.owner.id], None

    async def reconcile(self, group: str | None = None) -> None:
        """Act on changed settings -- for one group, or every device -- before the change is answered: a scope that
        cannot have a simulator now has its device ended, one whose connector or Xcode changed has it brought back on
        the new one, and new stream settings show at once."""
        withdrawn: list[DeviceInstance] = []
        async with self._lock:
            for instance in [i for i in self._instances.values() if group is None or i.group == group]:
                verdict, off = await self._govern(instance)
                if off:
                    await self._end(instance, shutdown=instance.may_shut_down, off=off)
                elif self._moved(instance, verdict):
                    await self._end(instance, shutdown=False, restarting=True)
                elif self._touch_withdrawn(instance, verdict):
                    withdrawn.append(instance)
                elif instance.hub is not None and instance.session is not None:
                    instance.hub.reconfigure(StreamSettings.from_config(verdict.config, instance.session.fps_limit))
        for instance in withdrawn:
            await self._view_only(instance)

    async def _view_only(self, instance: DeviceInstance) -> None:
        """Start a real device's session over view only, now that WebDriverAgent is turned off for it: under its own
        lock, once the manager's is let go. One ended meanwhile, or started over without WebDriverAgent already, is
        left as it is."""
        async with instance.session_lock:
            session = instance.session
            if self.is_current(instance) and instance.live and session is not None and session.input is not None:
                logger.info("starting the session on the device %s over view only: WebDriverAgent is turned off",
                            instance.udid)  # fmt: skip
                await self._start_over(instance)

    def _moved(self, instance: DeviceInstance, verdict: Verdict) -> bool:
        """Whether the owner's settings now put the device somewhere its session cannot follow: on another connector,
        or on another Xcode -- a companion keeps the SimulatorKit it started with, so only starting again changes it."""
        other_connector = verdict.connector is not None and verdict.connector.name != instance.chosen
        xcode = self.backends[instance.kind].developer_dir(verdict.config)
        return other_connector or xcode != instance.developer_dir

    async def reap(self) -> list[str]:
        """End what was switched off, shut down elsewhere or left idle, and attach again what lost its connector.
        Answers the UDIDs ended."""
        ended: list[str] = []
        withdrawn: list[DeviceInstance] = []
        async with self._lock:
            now = self._clock()
            gone = await self._gone()
            for instance in list(self._instances.values()):
                verdict, off = await self._govern(instance)
                if off:
                    await self._end(instance, shutdown=instance.may_shut_down, off=off)
                    ended.append(instance.udid)
                    continue
                if instance.udid in gone:
                    # Before a connector that stopped is started again: there is no device left to attach it to.
                    await self._end_gone(instance)
                    ended.append(instance.udid)
                    continue
                if instance.live and self._session_gone(instance):
                    # Nobody watching sees no stream fail, so the reaper is what notices a connector that stopped.
                    if instance.kind != "physical":
                        self._restart(instance, "its connector stopped")
                        continue
                    self._ended_by_itself(instance)
                if self._touch_withdrawn(instance, verdict):
                    # Turned off where no reconcile was told: a hand-edited file, the environment.
                    withdrawn.append(instance)
                if instance.viewers or instance.busy or self.usage.in_use(instance):
                    instance.last_used = now
                    continue
                if now - instance.last_used >= verdict.config.idle_minutes * 60:
                    await self._end(instance, shutdown=verdict.config.shutdown_on_idle and instance.may_shut_down)
                    ended.append(instance.udid)
        for instance in withdrawn:
            # One whose session is being started over is not waited for: the reaper runs again in a minute.
            if not instance.session_lock.locked():
                await self._view_only(instance)
        return ended

    def _ended_by_itself(self, instance: DeviceInstance) -> None:
        """A real device whose session ended with no call asking for it: left so -- WebDriverAgent killed on it means
        a person took the phone back -- its screens told, until the next call starts it over (`ensure`). It is ended as
        idle as any device is, once nobody watches or holds it."""
        if instance.fallback_reason == SESSION_ENDED_NOTE:
            return
        logger.info("the session on the device %s ended; it starts again on the next call", instance.udid)
        instance.fallback_reason = SESSION_ENDED_NOTE
        self.publish_status(instance)

    async def improve(self) -> list[str]:
        """Attach a better session for each running device whose session says it could now be reached better -- a real
        device's cable plugged back in. Each is asked at once, since one may wait a while for its answer; a device a
        build or a test run is using is not asked, as a new session would cut the run off, and is asked once the run
        is done. Answers the UDIDs a better session is being attached for."""
        asked = [
            (instance, instance.session)
            for instance in list(self._instances.values())
            if instance.session is not None and instance.session.better is not None
            if instance.live and not instance.busy
        ]
        answers = await asyncio.gather(*(self._better(instance, session) for instance, session in asked))
        found: list[str] = []
        for (instance, session), better in zip(asked, answers, strict=True):
            if better and instance.session is session and self._upgrade_soon(instance, session):
                logger.info("the %s %s can be reached better now: attaching it again", instance.kind, instance.udid)
                found.append(instance.udid)
        return found

    @staticmethod
    async def _better(instance: DeviceInstance, session: DeviceSession) -> bool:
        assert session.better is not None
        try:
            return await session.better()
        except (ConnectorError, DeviceControlError, OSError) as exc:
            logger.info("could not ask whether %s can be reached better: %s", instance.udid, exc)
            return False

    def _upgrade_soon(self, instance: DeviceInstance, session: DeviceSession) -> bool:
        """Attach a better session for the device in the background, unless it is already being attached again."""
        if instance.recovery is not None and not instance.recovery.done():
            return False
        instance.recovery = asyncio.get_running_loop().create_task(self._upgrade(instance, session))
        return True

    async def _upgrade(self, instance: DeviceInstance, old: DeviceSession) -> None:
        """Attach a second session beside the device's own and put it in its place once it is up. When the new one
        cannot be had -- the device not yet reachable a moment after its cable went back in -- the one it has is kept,
        and used as it was, rather than the device being left with none. Under the device's own lock, as a start-over
        is (`_revived`)."""
        async with instance.session_lock:
            if not self.is_current(instance) or instance.session is not old or not instance.live:
                return
            connector = self.availability.registry.get(instance.connector)
            if connector is None:
                return
            told = (instance.connector, instance.capabilities, instance.fallback_reason)
            try:
                session = await self._attach(instance, self._config.get(instance.owner), [(connector, None)])
                try:
                    screen = await session.screen.describe()
                except ConnectorError:
                    await session.close()
                    raise
            except ConnectorError as exc:
                instance.connector, instance.capabilities, instance.fallback_reason = told
                logger.info("the %s %s keeps its session: a better one could not be had: %s", instance.kind,
                            instance.udid, exc)  # fmt: skip
                return
            if not self.is_current(instance):
                # Ended while the better one was attached: its end closed the old one.
                await session.close()
                return
            instance.session, instance.screen = session, screen
            if instance.hub is not None:
                instance.hub.rebind(session.screen, screen)
            await old.close()
            logger.info("attached the %s %s again, better", instance.kind, instance.udid)
            self._told_again(instance)

    async def start_at_boot(self) -> int:
        """End whatever a previous run of this host left behind, connector by connector. Answers how much there was."""
        ended = 0
        for connector in self.availability.registry.connectors():
            try:
                ended += await connector.reap_orphans()
            except (ConnectorError, OSError) as exc:
                logger.warning("the %s connector could not clean up after a previous run: %s", connector.name, exc)
        await self.put_back_left()
        return ended

    async def put_back_left(self) -> list[str]:
        """Put back what a previous run changed on devices and ended without putting back. What cannot be put back
        now -- a device that is not connected -- stays written down for the next start. Answers the devices put
        back."""
        done: list[str] = []
        for udid, left in self._journal.pending().items():
            kind = kind_of(udid)
            backend = self.backends.get(kind) if kind is not None else None
            if backend is None:
                continue
            failed = await undo(backend.control(left.developer_dir), udid, left.changes)
            self._journal.write(udid, Left(left.developer_dir, {what: left.changes[what] for what in failed}))
            if not failed:
                logger.info("put back what a previous run changed on %s", udid)
                done.append(udid)
        return done

    async def shutdown(self) -> None:
        """Let go of every device's session, leaving the devices themselves running for the next start."""
        async with self._lock:
            for instance in list(self._instances.values()):
                await self._end(instance, shutdown=False)
