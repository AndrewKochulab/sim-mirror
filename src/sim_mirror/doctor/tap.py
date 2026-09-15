# SPDX-License-Identifier: Apache-2.0
"""The doctor's real test: open Settings on a simulator, tap General, and see whether the screen changed.

Everything else the doctor checks can look fine while touches go nowhere -- Xcode 27's Device Hub swallows them without
a word -- so this one does what an agent would. It uses ``--device``, else a booted iOS simulator, else one of its own
(named for the ``Doctor`` scope); launches Settings fresh; taps the General row; and waits `TAP_WAIT_MS` for General's
page (its About row). A screen that did not change means input was swallowed, and says what to do.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sim_mirror.connectors.base import Capability, ConnectorError
from sim_mirror.core.actions import ActionError
from sim_mirror.core.instance import READY, STALLED, DeviceInstance
from sim_mirror.core.manager import SimulatorUnavailable
from sim_mirror.core.runtime import Runtime
from sim_mirror.doctor.macos import DEVICE_HUB_FIX
from sim_mirror.doctor.report import CheckResult
from sim_mirror.platform.simctl import SimctlError
from sim_mirror.scope import Scope
from sim_mirror.seams import Caller

NAME = "test tap"
SETTINGS_APP = "com.apple.Preferences"
TAP_SCOPE = Scope(id="sim-mirror-doctor", group="sim-mirror-doctor", label="Doctor")
CALLER = Caller(TAP_SCOPE, key="doctor", title="sim-mirror doctor")
READY_WAIT_S = 120.0
READY_POLL_S = 0.5
OPEN_WAIT_MS = 5000
TAP_WAIT_MS = 3000
SNAPSHOT_ELEMENTS = 120
SWALLOWED = f"Input was swallowed. {DEVICE_HUB_FIX}"


async def _ready(instance: DeviceInstance, sleep: Callable[[float], Awaitable[None]]) -> str | None:
    """None once the device is ready; otherwise why it is not."""
    waited = 0.0
    while instance.state not in (READY, STALLED):
        if not instance.live:
            return f"the simulator {instance.state}: {instance.reason or 'no reason was given'}"
        if waited >= READY_WAIT_S:
            return f"the simulator was still {instance.state} after {round(waited)}s"
        await sleep(READY_POLL_S)
        waited += READY_POLL_S
    return None


async def _device(runtime: Runtime, device: str | None) -> None:
    """Point the doctor's scope at the device to tap: the one named, else a booted one, else its own."""
    manager = runtime.manager
    if device is None:
        booted = [choice for choice in await manager.devices(TAP_SCOPE) if choice["state"] == "Booted"]
        device = booted[0]["udid"] if booted else None
    if device is not None:
        await manager.choose(TAP_SCOPE, device)


async def check_tap(
    runtime: Runtime | None, device: str | None, sleep: Callable[[float], Awaitable[None]]
) -> CheckResult:
    if runtime is None:
        return CheckResult(NAME, "skip", "skipped (--no-tap)")
    manager = runtime.manager
    try:
        await _device(runtime, device)
        instance = await manager.ensure(TAP_SCOPE)
        not_ready = await _ready(instance, sleep)
        if not_ready:
            return CheckResult(NAME, "fail", not_ready)
        if Capability.INPUT_TOUCH not in instance.capabilities:
            return CheckResult(
                NAME,
                "warn",
                f"{instance.name} is shown through {instance.connector}, which cannot take touches; nothing to tap",
                "Install idb_companion (`brew install facebook/fb/idb-companion`) for touch, typing and reading "
                "the screen.",
            )
        await manager.simctl(instance).launch(instance.udid, SETTINGS_APP, terminate_running=True)
        await runtime.actions.act(
            instance, CALLER, [{"pause": 0}], wait={"for": "General", "timeout_ms": OPEN_WAIT_MS}, snapshot="none"
        )
        general = (await runtime.actions.read(instance, CALLER, SNAPSHOT_ELEMENTS)).find("General")
        if general is None:
            return CheckResult(
                NAME,
                "fail",
                f"Settings opened on {instance.name}, but its General row was not on screen to tap",
                "Unlock the simulator and close anything covering Settings, then run the doctor again.",
            )
        answer = await runtime.actions.act(
            instance, CALLER, [{"tap": general.ref}], wait={"for": "About", "timeout_ms": TAP_WAIT_MS}, snapshot="none"
        )
        if any(line.startswith("waited") for line in answer.splitlines()):
            return CheckResult(NAME, "ok", f"a tap on General reached {instance.name} through {instance.connector}")
        return CheckResult(
            NAME,
            "fail",
            f"a tap on General did not change {instance.name}'s screen within {TAP_WAIT_MS // 1000}s",
            SWALLOWED,
        )
    except (SimulatorUnavailable, ActionError, SimctlError, ConnectorError) as exc:
        return CheckResult(NAME, "fail", str(exc))
    finally:
        await manager.stop(TAP_SCOPE)
