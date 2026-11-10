# SPDX-License-Identifier: Apache-2.0
"""What about this Mac's session decides whether a simulator can be shown and touched.

* **Xcode 27's Device Hub**: a simulator it takes over can take input only through Device Hub's own transport
  (``dtuhidd``) and silently ignore the kind SimMirror's companion sends -- screenshots and the element tree still work,
  so nothing else looks wrong. Measured on Xcode 27.0 (27A266a): its process is ``DeviceHub``, and a device merely
  booted while it was open still took SimMirror's taps, so an open Device Hub is a warning to look, not a verdict.
* **A desktop session**: simulators need a logged-in user's graphical session (``Aqua``); over SSH or from a launch
  daemon they may not boot or show.
* **macOS app permissions**: the helper reports nonprompting Screen Recording and Accessibility preflight checks.
  Doctor reads that report because permissions belong to the signed helper, not this Python process.
"""

from __future__ import annotations

import json
from pathlib import Path

from sim_mirror.doctor.real_devices import _tail_lines, _written
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


#: What keeps a grant across upgrades: the installed helper signed with this Mac's certificate
#: (`platform.local_signing`).
SIGN_FIRST = "First run `sim-mirror helper sign` once, so the grant is kept across upgrades. "


def check_permission(name: str, log_dir: Path | None, helper: Path | None = None, *, kept: bool = True) -> CheckResult:
    """Read the helper's last report, without starting it or asking macOS for a permission. `helper` is the installed
    helper's path -- the one to allow -- and `kept` whether it is signed so a grant survives an upgrade."""
    pane = name.title() if name != "screen recording" else "Screen Recording"
    allow = (
        ("" if kept else SIGN_FIRST) + f"Allow {helper} in System Settings > Privacy & Security > {pane}."
        if helper is not None
        else "Grant the helper access in System Settings > Privacy & Security."
    )
    key = name.replace(" ", "_")
    if log_dir is not None:
        for path in sorted(log_dir.glob("native-*.log"), key=_written, reverse=True):
            for line in reversed(_tail_lines(path)):
                if "mac-permissions: " not in line:
                    continue
                try:
                    report = json.loads(line.split("mac-permissions: ", 1)[1])
                    parts = report.get("parts", [])
                except (ValueError, AttributeError):
                    continue
                for part in parts if isinstance(parts, list) else []:
                    if isinstance(part, dict) and part.get("name") == key and isinstance(part.get("ok"), bool):
                        ok = part["ok"]
                        return CheckResult(
                            name,
                            "ok" if ok else "warn",
                            "helper's last report: " + ("granted" if ok else "not granted"),
                            "" if ok else allow,
                        )
    return CheckResult(name, "skip", "not checked: no macOS app helper permission report; no prompt was requested")


def check_accessibility(log_dir: Path | None = None, helper: Path | None = None, *, kept: bool = True) -> CheckResult:
    return check_permission("accessibility", log_dir, helper, kept=kept)
