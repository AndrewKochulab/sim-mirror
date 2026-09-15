# SPDX-License-Identifier: Apache-2.0
"""What about this Mac's session decides whether a simulator can be shown and touched.

* **Xcode 27's Device Hub**: a simulator booted while it is open takes input only through Device Hub's own transport
  (``dtuhidd``), and silently ignores the kind SimMirror's companion sends -- screenshots and the element tree still
  work, so nothing else looks wrong.
* **A desktop session**: simulators need a logged-in user's graphical session (``Aqua``); over SSH or from a launch
  daemon they may not boot or show.
* **Accessibility**: reading whether this process may use it would show macOS's permission prompt, so it is reported
  as not checked rather than asked.
"""

from __future__ import annotations

from sim_mirror.doctor.report import CheckResult
from sim_mirror.platform.xcode import Runner

DEVICE_HUB = "Device Hub"
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
    if await running(run, DEVICE_HUB):
        return CheckResult(
            "device hub",
            "warn",
            "Xcode's Device Hub is open: a simulator booted while it is open ignores SimMirror's touches",
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
