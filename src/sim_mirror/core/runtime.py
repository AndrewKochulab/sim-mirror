# SPDX-License-Identifier: Apache-2.0
"""The composition root: one SimMirror, put together from a host's seams -- or a standalone install's.

A host builds one `Runtime` per process (`Runtime.build`), starts it once its event loop runs, tells it when settings
change (`reconcile`, awaited before the change is answered, so a switch that is off is off by then), and closes it on
the way out. Routers, the daemon and the CLI reach devices, agent tools and builds through it, and nothing else puts
those together.

Whether a scope's agents may use their tools is answered here, on every manifest and every call, from the settings as
they are now: a simulator, its agent tools or the host's area switched off since an agent connected answers with why,
not with a device. So is which device a call acts on -- the calling agent's own (`core.selections`) -- and whether
another agent drives that device (`core.drivers`) or works on it: a call that would change a device another live agent
works on is refused before it runs where ``device.exclusive`` says so, and any other call there starts its answer by
saying who works on it.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sim_mirror.build.folders import Folders
from sim_mirror.build.gradle import GradleToolchain
from sim_mirror.build.xcodebuild import BuildRunner
from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.app.merge import AppHierarchyMerge
from sim_mirror.connectors.base import Capability
from sim_mirror.connectors.iphone.wda_setup import WdaSetup
from sim_mirror.connectors.mcpbridge.merge import HierarchyMerge
from sim_mirror.connectors.registry import ConnectorContext, ConnectorRegistry
from sim_mirror.core import activity
from sim_mirror.core.actions import AgentActions
from sim_mirror.core.android_backend import AndroidBackend
from sim_mirror.core.android_devices import AndroidServices
from sim_mirror.core.availability import Availability
from sim_mirror.core.backends import PhysicalBackend, SimulatorBackend
from sim_mirror.core.device_changes import ChangeJournal
from sim_mirror.core.device_logs import DeviceLogBook
from sim_mirror.core.devices import DeviceDirectory
from sim_mirror.core.drivers import Drivers, Held, changes_device, exclusive
from sim_mirror.core.instance import DeviceInstance
from sim_mirror.core.mac_backend import MacBackend
from sim_mirror.core.manager import DeviceManager, SimulatorUnavailable
from sim_mirror.core.reaper import Reaper
from sim_mirror.core.recordings import Recordings
from sim_mirror.core.render import HelperRenderer
from sim_mirror.core.screen_relay import ScreenRelay, ScreenSocket
from sim_mirror.core.selections import Selections
from sim_mirror.core.signing import SigningTeams
from sim_mirror.core.touch import ScrcpyTouchSetup, TouchSetups
from sim_mirror.host_copy import HostCopy
from sim_mirror.perception.ocr import OcrReaders, TextRecognizer
from sim_mirror.perception.readers import CombinedExtraReaders, ExtraReaders
from sim_mirror.perception.vision.helper import VisionHelpers
from sim_mirror.platform.devicectl import Devicectl
from sim_mirror.platform.identifiers import PHONE_PREFIX, kind_of
from sim_mirror.platform.keyboard import KeyboardCheck, mac_keyboard_is_us
from sim_mirror.platform.macos import MacOS
from sim_mirror.platform.simctl import Simctl, Starter
from sim_mirror.platform.xcode import xcode_installed
from sim_mirror.platform.xcrun import XcrunRunner, run_xcrun, start_xcrun
from sim_mirror.protocol import DeviceKind
from sim_mirror.scope import Scope
from sim_mirror.seams import Caller, ConfigSource, DeviceMemory, InputPause, Policy, StateStore, UsageProbe
from sim_mirror.storage.app_support import helpers_dir
from sim_mirror.storage.claims import Claims
from sim_mirror.tools.context import ToolContext
from sim_mirror.tools.registry import ToolRegistry
from sim_mirror.tools.results import Result, text

#: Where what each device still has changed is written down, in the host's run folder (`core.device_changes`).
CHANGES_LEFT = "device-changes.json"
#: The calls that never give an agent that has no device yet one: they list, say or choose devices, or recordings.
UNSETTLED = frozenset(
    {("sim_device", "list"), ("sim_device", "info"), ("sim_device", "choose"), ("sim_record", "list")}
    | {("sim_record", "status")}
)


def default_recordings(
    xcrun: XcrunRunner,
    copy: HostCopy,
    *,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    start: Starter = start_xcrun,
) -> Recordings:
    """Recordings made by simctl for a simulator, from the frames SimMirror streams otherwise, rendered by the native
    helper."""

    async def record_with_simctl(instance: DeviceInstance, path: Path, log: Path, codec: str) -> Any:
        simctl = Simctl(xcrun, developer_dir=instance.developer_dir, start=start)
        return await simctl.start_recording(instance.udid, path, codec=codec, log_path=log)

    return Recordings(HelperRenderer(copy=copy), tool_recorder=record_with_simctl, clock=clock, sleep=sleep)


@dataclass
class Runtime:
    config: ConfigSource
    state: StateStore
    policy: Policy
    copy: HostCopy
    registry: ConnectorRegistry
    manager: DeviceManager
    actions: AgentActions
    tools: ToolRegistry
    builds: BuildRunner
    reaper: Reaper
    #: Every device's recording of its screen.
    recordings: Recordings
    #: Which team signs for each scope's real device.
    signing: SigningTeams
    #: Whether each scope's real device can be touched, and setting that up.
    touch: TouchSetups
    sleep: Callable[[float], Awaitable[None]] = field(default=asyncio.sleep)
    #: Which agent drives each device.
    drivers: Drivers = field(default_factory=Drivers)
    #: Which device each agent works on.
    selections: Selections = field(default_factory=Selections)
    #: The folder each agent named for its builds.
    folders: Folders = field(default_factory=Folders)

    @classmethod
    def build(
        cls,
        *,
        config: ConfigSource,
        state: StateStore,
        policy: Policy,
        memory: DeviceMemory,
        copy: HostCopy | None = None,
        usage: UsageProbe | None = None,
        registry: ConnectorRegistry | None = None,
        claims: Claims | None = None,
        tools: ToolRegistry | None = None,
        builds: BuildRunner | None = None,
        xcrun: XcrunRunner = run_xcrun,
        keyboard_is_us: KeyboardCheck = mac_keyboard_is_us,
        hierarchy: ExtraReaders | None = None,
        vision: TextRecognizer | None = None,
        may_share: Callable[[Scope, Scope], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        platform: str = sys.platform,
        recordings: Recordings | None = None,
        signing: SigningTeams | None = None,
        wda_setup: WdaSetup | None = None,
        pause: InputPause | None = None,
        android: AndroidServices | None = None,
        mac: MacOS | None = None,
        has_xcode: Callable[[], bool] = xcode_installed,
    ) -> Runtime:
        """SimMirror over these seams.

        A host passes `config`, `state`, `policy` and `memory`, and may pass `copy`, `usage`, `may_share` and `pause`:
        those are the stable part of this call. `registry`, `claims`, `tools`, `builds`, `xcrun`, `keyboard_is_us`,
        `hierarchy`, `vision`, `clock`, `sleep`, `platform`, `recordings`, `signing`, `wda_setup`, `android` and
        `has_xcode` are how SimMirror's own tests put a runtime together, and may change in a minor release
        (`docs/stability.md`).

        `android` reaches Android emulators and phones -- one adb client, and the emulators this host started -- and
        `has_xcode` says whether this Mac can run a simulator: one with no Xcode and the Android SDK gives a scope
        that remembers no device an Android one.

        `signing` says which team signs for each scope's real device (`core.signing`): by default the project in the
        folder the host's policy builds the scope in, then ``real_devices.team_id``, then this Mac's only team.
        `wda_setup` sets WebDriverAgent up for a real device, for its connector and a person's Set up touch alike.

        `recordings` records devices' screens; by default through simctl for a simulator and the frames SimMirror
        streams otherwise, rendered by the native helper.

        `memory` is asked for rather than defaulted: where a scope's device is remembered is a decision, and a host
        given one silently would find a JSON file it never chose. A standalone install passes
        ``JsonDeviceMemory(state.devices_file())``; a host with somewhere better passes its own.

        `hierarchy` is what snapshots merge in besides a connector's own tree: by default the view hierarchy the app in
        front shares through SimMirror's debug SDK, for the scopes whose ``connectors.app.merge`` is on, then Xcode 27's
        UI hierarchy, for those whose ``connectors.mcpbridge.merge`` is.

        `vision` reads text in a screen's pixels, for the scopes whose ``perception.ocr`` is on: by default SimMirror's
        Swift helper (`perception.vision`), compiled on first use under the state folder, on a Mac only.

        `may_share` says whether two scopes may use one device; by default any two may. A daemon serving several hosts
        answers it so each host's devices stay its own.

        `pause` says when a person has paused agents' gestures on a device -- for a host that knows its person has a
        real phone in hand, whose touches no viewer sees. By default agents give way to a viewer's input only.
        """
        copy = copy or HostCopy()

        def simctl_for(developer_dir: str) -> Simctl:
            return Simctl(xcrun, developer_dir=developer_dir)

        #: Each cabled real device's log: kept by its connector, read by its control.
        device_logs = DeviceLogBook()
        wda_setup = wda_setup or WdaSetup(xcrun=xcrun)
        #: One adb client, the Android devices it lists and the emulators this host started: for the backend and the
        #: connector alike.
        android = android or AndroidServices.build(state)
        mac = mac or MacOS()
        signing = signing or SigningTeams(policy.build_folder)
        registry = registry or ConnectorRegistry.discover(
            ConnectorContext(
                state=state,
                copy=copy,
                simctl_for=simctl_for,
                xcrun=xcrun,
                device_logs=device_logs,
                wda_setup=wda_setup,
                android=android,
                mac=mac,
            )
        )
        availability = Availability(config=config, policy=policy, registry=registry, copy=copy, platform=platform)
        directory = DeviceDirectory(memory, copy)

        def devicectl_for(developer_dir: str) -> Devicectl:
            return Devicectl(xcrun, developer_dir=developer_dir)

        def default_kind() -> DeviceKind:
            return "android" if not has_xcode() and android.sdk() is not None else "simulator"

        manager = DeviceManager(
            config=config,
            availability=availability,
            directory=directory,
            claims=claims or Claims(state.claims_dir(), owner=copy.owner_name),
            simctl_for=simctl_for,
            copy=copy,
            usage=usage,
            keyboard_is_us=keyboard_is_us,
            may_share=may_share,
            clock=clock,
            sleep=sleep,
            backends={
                "simulator": SimulatorBackend(simctl_for, directory),
                "physical": PhysicalBackend(devicectl_for, copy=copy, logs=device_logs),
                "android": AndroidBackend(android, copy=copy, clock=clock, sleep=sleep),
                "mac": MacBackend(mac),
            },
            journal=ChangeJournal(state.run_dir() / CHANGES_LEFT),
            signing=signing,
            default_kind=default_kind,
        )
        wda_setup.on_ready = manager.reattach

        def scrcpy_ready() -> None:
            """scrcpy's server was set up: every Android device shown through adb alone is attached again with it."""
            for instance in manager.instances():
                if instance.kind == "android" and Capability.STREAM_H264 not in instance.capabilities:
                    manager.reattach(instance.udid)

        android.jars.on_ready = scrcpy_ready
        runtime = cls(
            config=config,
            state=state,
            policy=policy,
            copy=copy,
            registry=registry,
            manager=manager,
            actions=AgentActions(
                manager,
                config,
                clock=clock,
                sleep=sleep,
                extra=hierarchy
                or CombinedExtraReaders(
                    AppHierarchyMerge(copy=copy, on_share=manager.share_app), HierarchyMerge(copy=copy)
                ),
                pixels=OcrReaders(
                    vision or VisionHelpers(folder=helpers_dir(os.environ), xcrun=xcrun), supported=platform == "darwin"
                ),
                pause=pause,
                copy=copy,
            ),
            tools=tools or ToolRegistry(),
            builds=builds
            or BuildRunner(
                state,
                xcrun=xcrun,
                copy=copy,
                others={"android": GradleToolchain(android.directory.serial, copy=copy)},
            ),
            reaper=Reaper(manager, sleep=sleep),
            sleep=sleep,
            drivers=Drivers(copy, clock=clock),
            selections=Selections(config, clock=clock),
            recordings=recordings or default_recordings(xcrun, copy, clock=clock, sleep=sleep),
            signing=signing,
            touch=TouchSetups(
                manager,
                config,
                signing,
                wda_setup,
                copy,
                others={"android": ScrcpyTouchSetup(manager, config, android.jars)},
            ),
        )
        # Settings can change without a reconcile -- a hand-edited file, the environment, a host's policy -- so the
        # reaper also ends the builds they no longer allow.
        runtime.reaper.add(runtime.stop_builds_not_allowed)
        return runtime

    def __post_init__(self) -> None:
        # A device an agent chose is held for its scope's agents while one works on it (`DeviceManager.bound`).
        self.manager.bound = self.selections.bound_to
        # A person picking the scope's own device moves the agents working on the old one with it.
        self.manager.followed = self.selections.follow

    # -- lifetime ---------------------------------------------------------------------------------------------------

    async def start(self) -> None:
        """End what an earlier run left behind, then keep ending what is switched off or idle."""
        await self.manager.start_at_boot()
        self.reaper.start()

    async def close(self) -> None:
        """Stop reaping, end every build, and let go of every device -- the devices themselves keep running."""
        await self.reaper.stop()
        await self.builds.shutdown()
        await self.touch.shutdown()
        await self.manager.shutdown()
        await self.actions.close()

    async def reconcile(self, group: str | None = None) -> None:
        """Act on changed settings for one group of scopes, or all: devices first, then builds that may not run now."""
        await self.manager.reconcile(group)
        await self.stop_builds_not_allowed(group)

    async def stop_builds_not_allowed(self, group: str | None = None) -> None:
        """End the builds a scope may not run now: its simulator or its build tools switched off, or the host no
        longer allowing it commands."""
        for run in self.builds.runs():
            scope = run.scope
            if group is not None and scope.group != group:
                continue
            allowed = self.config.get(scope).build_tools and self.policy.shells_allowed(scope)
            if await self.manager.unavailable(scope) or not allowed:
                await self.builds.cancel(scope.id)

    # -- agents -----------------------------------------------------------------------------------------------------

    async def refusal(self, scope: Scope) -> str | None:
        """Why this scope's agents may not use their tools right now, or None."""
        reason = await self.manager.unavailable(scope)
        if reason:
            return reason
        if not self.config.get(scope).agent_tools:
            return self.copy.agent_tools_off()
        return None

    async def manifest(self, scope: Scope) -> dict[str, Any]:
        """The tools this scope's agents are offered and how to use them -- or none, and why."""
        refused = await self.refusal(scope)
        if refused:
            return {"tools": [], "instructions": refused}
        # Every tool stays listed whatever the device can do now -- a real device is touched only while WebDriverAgent
        # drives it, which comes and goes with its cable -- and a call says why it cannot be made
        # (`tools.context.require`): a client that keeps the tools it was first given would otherwise never hear that
        # they came back, and an agent offered none could not tell the person what to do.
        return self.tools.manifest(self.config.get(scope))

    def tool_context(self, caller: Caller) -> ToolContext:
        """What a call by this agent runs with, read from the settings and the host's policy as they are now."""
        scope = caller.scope
        config = self.config.get(scope)
        return ToolContext(
            manager=self.manager,
            actions=self.actions,
            caller=caller,
            config=config,
            roots=self.policy.install_roots(scope),
            copy=self.copy,
            sleep=self.sleep,
            builds=self.builds,
            folder=self._build_folder(caller),
            settings_for=lambda folder: self._folder_settings(scope, folder),
            shells_allowed=self.policy.shells_allowed(scope),
            recordings=self.recordings,
            signing=self.signing,
            selections=self.selections,
            per_agent=config.device_selection == "agent",
            folders=self.folders,
        )

    def _build_folder(self, caller: Caller) -> Path | None:
        """The folder this agent's builds find their project in: the first folder its own token names, where its host
        says (`Caller.home`), rather than the first any agent's token of its scope names -- or none, where the host
        says its scope builds nothing."""
        folder = self.policy.build_folder(caller.scope)
        return caller.home if folder is not None and caller.home is not None else folder

    def _folder_settings(self, scope: Scope, folder: Path) -> SimConfig:
        """The settings a scope's build in another folder runs with -- a git worktree of its project, say: that
        folder's own, as `sim-mirror mcp` started there would read them, when a person gave it some
        (``[scopes."<its id>"]``); otherwise the scope's. A source that cannot say where its values come from
        (`SettingsStore.explain`) gives the scope's."""
        own = Scope.for_folder(folder)
        explain = getattr(self.config, "explain", None) if own.id != scope.id else None
        if callable(explain) and any(origin.layer == "scope" for origin in explain(own).values()):
            return self.config.get(own)
        return self.config.get(scope)

    async def call(self, caller: Caller, name: object, arguments: object) -> Result:
        """Run one tool call for this agent -- or answer why not, as an error result: among the reasons, another agent
        drives the device a call that changes it would change (`core.drivers`), or works on it (`core.selections`).
        What the agent should know first -- who else works on its device -- starts the answer."""
        refused = await self.refusal(caller.scope)
        if refused:
            return text(refused, error=True)
        self.selections.begin(caller)
        try:
            return await self._call(caller, name, arguments)
        finally:
            self.selections.end(caller)

    async def _call(self, caller: Caller, name: object, arguments: object) -> Result:
        changing = changes_device(name, arguments)
        steal = arguments.get("steal") if isinstance(arguments, dict) else None
        if changing and steal is not None and not isinstance(steal, bool):
            return text("steal is true or false", error=True)
        action = arguments.get("action") if isinstance(arguments, dict) else None
        per_agent = self.config.get(caller.scope).device_selection == "agent"
        notes: list[str] = []
        if per_agent and (name, action) not in UNSETTLED and self.selections.of(caller) is None:
            notes += await self._settle(caller)
        try:
            if per_agent and name == "sim_device" and action == "choose":
                assert isinstance(arguments, dict)
                notes += self._choosing(caller, arguments.get("udid"), steal=steal is True)
            else:
                quiet = (name, action) == ("sim_device", "list")
                notes += self._acting(caller, changing=changing, steal=steal is True, per_agent=per_agent, quiet=quiet)
        except Held as exc:
            return text(str(exc), error=True)
        try:
            async with self.actions.working(caller, activity.of_call(name, arguments), udid=self._device_of(caller)):
                result = await self.tools.call(name, arguments, self.tool_context(caller))
        finally:
            if changing:
                instance = self.manager.instance(caller.scope, self.selections.of(caller) if per_agent else None)
                if instance is not None:
                    self.drivers.done(caller, instance)
        if notes:
            said = result["content"][0]
            said["text"] = "".join(f"note: {note}\n" for note in notes) + said["text"]
        return result

    def _device_of(self, caller: Caller) -> Callable[[], str | None]:
        """The device a call of this agent's acts on, asked each time: its binding can change while the call runs."""

        def udid() -> str | None:
            per_agent = self.config.get(caller.scope).device_selection == "agent"
            return self.selections.of(caller) if per_agent else None

        return udid

    def _real(self, udid: str) -> bool:
        """Whether a device is a real one -- an iPhone, an iPad, an Android phone -- rather than one on this Mac."""
        running = self.manager.running(udid)
        if running is not None:
            return not self.manager.runs_here(running)
        kind = kind_of(udid)
        return kind == "physical" or (kind == "android" and udid.startswith(f"{PHONE_PREFIX}-"))

    def _exclusive(self, caller: Caller, udid: str) -> bool:
        running = self.manager.running(udid)
        mode = self.config.get(running.owner if running is not None else caller.scope).device_exclusive
        return exclusive(mode, real=self._real(udid))

    def _acting(self, caller: Caller, *, changing: bool, steal: bool, per_agent: bool, quiet: bool) -> list[str]:
        """Who else works on the device this call acts on: refused with `Held` when the call would change it and one
        agent at a time drives it, else what to tell the agent first -- unless the call is `quiet`, a listing that says
        so itself. A call that changes it takes it over."""
        mine = self.selections.of(caller) if per_agent else None
        instance = self.manager.instance(caller.scope, mine)
        udid = instance.udid if instance is not None else mine or self.manager.default_udid(caller.scope)
        notes: list[str] = []
        if udid is None:
            return notes
        holder = self.drivers.holder(caller, instance) if changing and instance is not None else None
        occupant = None
        if per_agent and not (instance is not None and self.drivers.drives(caller, instance)):
            occupant = self.selections.occupant(caller, udid)
        if occupant is not None and holder is None:
            if changing and self._exclusive(caller, udid) and not steal:
                device = instance.name if instance is not None else "this device"
                raise Held(self.copy.device_bound(device, occupant.title, self.selections.quiet_s(occupant)))
            if not quiet and self.selections.tell(caller, occupant):
                notes.append(self.copy.works_on(occupant.title, self.selections.quiet_s(occupant)))
        if changing and instance is not None:
            note = self.drivers.take(caller, instance, exclusive=self._exclusive(caller, instance.udid), steal=steal)
            notes += [note] if note else []
        return notes

    def _choosing(self, caller: Caller, chosen: object, *, steal: bool) -> list[str]:
        """Whether an agent may choose this device for itself: refused with `Held` when another live agent works on it
        or drives it and one agent at a time drives it, else what to tell the agent first. Its own device is not
        changed for anyone else, so only the one it would choose is asked about."""
        if not isinstance(chosen, str) or not chosen:
            return []
        running = self.manager.running(chosen)
        holder = self.drivers.holder(caller, running) if running is not None else None
        working = self.selections.on(chosen, besides=caller.key)
        if holder is None and not working:
            return []
        if self._exclusive(caller, chosen) and not steal:
            if holder is not None and running is not None:
                raise self.drivers.held(holder, running)
            name = running.name if running is not None else "this device"
            raise Held(self.copy.device_bound(name, working[0].title, self.selections.quiet_s(working[0])))
        if holder is not None and running is not None:
            return [self.drivers.take(caller, running, exclusive=False, steal=True)]
        return [self.copy.works_on(working[0].title, self.selections.quiet_s(working[0]))]

    async def _settle(self, caller: Caller) -> list[str]:
        """Give an agent that works on no device yet one nobody uses, when ``device.agent_default`` says ``free`` and
        another live agent works on its scope's own: what to tell it, when it was given one."""
        scope = caller.scope
        if self.config.get(scope).agent_default != "free":
            return []
        default = self.manager.default_udid(scope)
        working = self.selections.on(default, besides=caller.key) if default is not None else []
        if not working:
            return []
        try:
            listed = await self.manager.devices(scope)
        except SimulatorUnavailable:
            return []
        taken = {instance.udid for instance in self.manager.instances() if instance.live}
        # One of the runtime the scope's own runs, first: the app built for it runs there too.
        runtime = next((choice["runtime"] for choice in listed if choice["udid"] == default), None)
        for choice in sorted(listed, key=lambda choice: choice["runtime"] != runtime):
            here = choice["kind"] == "simulator" or (choice["kind"] == "android" and choice["connection"] is None)
            udid = choice["udid"]
            if here and choice["usable"] and udid != default and udid not in taken and not self.selections.on(udid):
                self.selections.bind(caller, udid)
                return [self.copy.given_free(choice["name"], working[0].title)]
        return []

    def let_go(self, key: str) -> list[str]:
        """An agent's connection ended -- its token revoked as its MCP server closes: the devices it drove pass over at
        once, the device it worked on is no longer held for it, and the folder it named for its builds is forgotten.
        Answers the udids it drove."""
        self.selections.forget(key)
        self.folders.forget(key)
        return self.drivers.let_go(key, self.manager.instances())

    # -- viewers ----------------------------------------------------------------------------------------------------

    def relay(self, socket: ScreenSocket, instance: DeviceInstance, scope: Scope | None = None) -> ScreenRelay:
        """A screen socket's relay to the device a scope's ticket opened, with the device's settings as they are now."""
        return ScreenRelay(socket, self.manager, instance, config=self.config.get(instance.owner), scope=scope)
