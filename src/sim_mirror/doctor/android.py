# SPDX-License-Identifier: Apache-2.0
"""The doctor's checks for Android emulators and phones: what a person can see is set up, and what to do if it is not.

* **android sdk** -- the SDK SimMirror found, its adb and emulator, whether adb's server answers, and the AVDs, with
  what stands in the way of one that cannot start;
* **android devices** -- the emulators running and the phones adb lists, and a phone that has to be allowed first;
* **scrcpy** -- whether scrcpy's server is on and set up (`sim-mirror android setup`), checked by its hash;
* **android builds** -- the JDK a Gradle build would run with, while build tools are on.

While ``android.enabled`` is off, or there is no SDK, the others say so and check nothing. None of them starts adb's
server, an emulator or anything on a device.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sim_mirror.doctor.report import CheckResult
from sim_mirror.platform.adb import AdbError
from sim_mirror.platform.android_sdk import AndroidSdk

if TYPE_CHECKING:
    from sim_mirror.doctor.checks import DoctorContext

TURN_ON = "`sim-mirror config set android.enabled true`"
INSTALL_SDK = "Install Android Studio, or name the SDK with android.sdk_path."
INSTALL_IMAGE = "Install each one's system image in Android Studio's SDK Manager, or delete it in its Device Manager."
ALLOW = "Unlock the phone and allow USB debugging when it asks."
SET_UP = "Press Set up touch in the viewer, or run `sim-mirror android setup`."


def sdk_of(ctx: DoctorContext) -> AndroidSdk | None:
    """The SDK SimMirror would use: None while Android is off, or when there is none."""
    return ctx.android_sdk(ctx.config.android_sdk_path) if ctx.config.android else None


def android_only(ctx: DoctorContext) -> bool:
    """Whether Android devices are what this Mac can use: on, with an SDK -- said where a missing Xcode would
    otherwise fail the doctor."""
    return sdk_of(ctx) is not None


def _skipped(name: str, ctx: DoctorContext) -> CheckResult | None:
    if not ctx.config.android:
        return CheckResult(name, "ok", "off with Android devices")
    if sdk_of(ctx) is None:
        return CheckResult(name, "skip", "needs the Android SDK")
    return None


async def check_android_sdk(ctx: DoctorContext) -> CheckResult:
    name = "android sdk"
    if not ctx.config.android:
        return CheckResult(name, "ok", f"off; {TURN_ON} lists this Mac's Android emulators")
    sdk = sdk_of(ctx)
    if sdk is None:
        return CheckResult(name, "ok", "no Android SDK on this Mac; Android emulators and phones need one", INSTALL_SDK)
    tools = f"adb {sdk.adb_version or 'missing'}, emulator {sdk.emulator_version or 'missing'} at {sdk.root}"
    try:
        server = f"adb's server answers (protocol {await ctx.adb().version()})"
    except (AdbError, OSError):
        server = "adb's server is not running; it starts with the first Android device"
    found = ctx.avds(sdk)
    listed = ", ".join(f"{avd.display_name}{f' ({avd.problem})' if avd.problem else ''}" for avd in found)
    avds = f"AVDs: {listed}" if found else "no AVDs yet: make one in Android Studio's Device Manager"
    detail = f"{tools}; {server}; {avds}"
    if sdk.adb_version is None:
        return CheckResult(name, "warn", detail, "Install the SDK's platform tools in Android Studio's SDK Manager.")
    if any(avd.problem for avd in found):
        return CheckResult(name, "warn", detail, INSTALL_IMAGE)
    return CheckResult(name, "ok", detail)


async def check_android_devices(ctx: DoctorContext) -> CheckResult:
    name = "android devices"
    skipped = _skipped(name, ctx)
    if skipped is not None:
        return skipped
    try:
        found = await ctx.adb().devices()
    except (AdbError, OSError):
        return CheckResult(name, "ok", "none running: adb's server is not running")
    phones_off = "" if ctx.config.android_phones else "; phones are off (android.phones)"
    listed = [device for device in found if ctx.config.android_phones or device.connection == "emulator"]
    if not listed:
        return CheckResult(name, "ok", f"none running or connected{phones_off}")
    said = "; ".join(f"{device.serial} ({device.connection}, {device.state})" for device in listed) + phones_off
    if any(device.state != "device" for device in listed):
        return CheckResult(name, "warn", said, ALLOW)
    return CheckResult(name, "ok", said)


async def check_scrcpy(ctx: DoctorContext) -> CheckResult:
    name = "scrcpy"
    skipped = _skipped(name, ctx)
    if skipped is not None:
        return skipped
    if not ctx.config.scrcpy:
        return CheckResult(name, "ok", "off (android.scrcpy.enabled): Android devices are shown and touched by adb")
    jar = ctx.scrcpy.find(ctx.config.scrcpy_path)
    if jar is None:
        why = ctx.scrcpy.why_not(ctx.config.scrcpy_path) or ""
        return CheckResult(name, "warn", f"{why}; until then adb shows the screen a few times a second", SET_UP)
    return CheckResult(name, "ok", f"release {jar.release.version} at {jar.path}, checked by its SHA-256")


async def check_gradle(ctx: DoctorContext) -> CheckResult:
    name = "android builds"
    skipped = _skipped(name, ctx)
    if skipped is not None:
        return skipped
    if not ctx.config.build_tools:
        return CheckResult(name, "ok", "off with build tools (build.tools)")
    jdk = await ctx.jdk(ctx.config.android_java_home)
    if jdk is None:
        fix = "Install Android Studio, or name a JDK with android.java_home."
        return CheckResult(name, "warn", "no JDK found to run Gradle with", fix)
    return CheckResult(name, "ok", f"JDK {jdk.version or 'of unknown version'} at {jdk.home} ({jdk.source})")
