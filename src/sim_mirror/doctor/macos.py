# SPDX-License-Identifier: Apache-2.0
"""What about this Mac's session decides whether a simulator can be shown and touched.

* **Xcode 27's Device Hub**: a simulator it takes over can take input only through Device Hub's own transport
  (``dtuhidd``) and silently ignore the kind SimMirror's companion sends -- screenshots and the element tree still work,
  so nothing else looks wrong. Measured on Xcode 27.0 (27A266a): its process is ``DeviceHub``, and a device merely
  booted while it was open still took SimMirror's taps, so an open Device Hub is a warning to look, not a verdict.
* **A desktop session**: simulators need a logged-in user's graphical session (``Aqua``); over SSH or from a launch
  daemon they may not boot or show.
* **Accessibility**: reading whether this process may use it would show macOS's permission prompt, so it is reported
  as not checked rather than asked.
"""

from __future__ import annotations

from sim_mirror.doctor.report import CheckResult
from sim_mirror.platform.process import Runner

#: Device Hub's process as Xcode 27.0 (27A266a) runs it -- ``Contents/Applications/DeviceHub.app`` -- then as it is
#: named on screen, in case a later Xcode renames the process to match.
DEVICE_HUB_PROCESSES = ("DeviceHub", "Device Hub")
DTUHIDD = "dtuhidd"
AQUA = "Aqua"
DEVICE_HUB_FIX = (
    "Close Device Hub, shut the simulator down, and boot it again with Device Hub closed; "
    "SimMirror's touches then reach it."
)


async def running(run: Runner, name: str) -> bool:
    code, _ = await run(("pgrep", "-x", name))
    return code == 0


async def check_device_hub(run: Runner) -> CheckResult:
    if any([await running(run, name) for name in DEVICE_HUB_PROCESSES]):
        return CheckResult(
            "device hub",
            "warn",
            "Xcode's Device Hub is open: a simulator it has taken over can ignore SimMirror's touches",
            DEVICE_HUB_FIX,
        )
    helper = " (its input helper dtuhidd is running)" if await running(run, DTUHIDD) else ""
    return CheckResult("device hub", "ok", f"Device Hub is not open{helper}")


async def check_gui_session(run: Runner) -> CheckResult:
    code, out = await run(("launchctl", "managername"))
    name = out.strip()
    if code != 0 or not name:
        return CheckResult("desktop session", "skip", "launchctl did not say which session this is")
    if name == AQUA:
        return CheckResult("desktop session", "ok", "a logged-in desktop session (Aqua)")
    return CheckResult(
        "desktop session",
        "warn",
        f"this is a {name} session, not a desktop one: simulators may not boot or show",
        "Run SimMirror from a logged-in user's session, not over SSH or from a launch daemon.",
    )


def check_accessibility() -> CheckResult:
    return CheckResult(
        "accessibility",
        "skip",
        "not checked: asking would show macOS's permission prompt, and SimMirror needs no grant",
    )
