# SPDX-License-Identifier: Apache-2.0
"""`DeviceControl` for an Android device -- an emulator or a phone -- through adb, and the emulator's console.

**Apps** are installed whole or from their split APKs, found by package name (``dumpsys package``), launched through
their launcher activity (``am start -W``) with their pid read after (``pidof``), ended with ``am force-stop``, and sent
URLs as a ``VIEW`` intent. Launch arguments are ``key=value`` pairs, each an intent extra: an Android app reads its
launch's extras, not a command line.

**The log** is ``logcat``'s, read from the device's own clock back: an app's lines are those its uid wrote, which
stays the same from one launch to the next, unlike its pid.

**How it looks** -- dark mode, the font scale, high-contrast text, animations, SystemUI's demo status bar, which way
the screen is turned -- is set through the device's settings. Each is read first exactly as the device has it
(`ExactRestore`) -- a font scale of 1.15, animations at half speed, a night mode on a schedule, a setting never set
(``null``, deleted again) -- so it is put back as it was. iOS's twelve text size categories are mapped onto Android's
font scale, 0.85 to 2.0.

**Where it is** is the emulator console's ``geo fix``; a route is a sequence of fixes along its waypoints at its speed,
one a second, until it ends or is cleared. A phone's location is not adb's to set, so a phone's session has no
``location`` capability.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import math
import re
from collections.abc import Awaitable, Callable, Mapping, MutableMapping, Sequence
from pathlib import Path
from typing import Any

from sim_mirror.core.control import TEXT_SIZES, DisplayState, InstalledApp
from sim_mirror.platform.adb import Adb, AdbError
from sim_mirror.platform.emulator import EmulatorConsole, port_of

#: iOS's text size categories, as Android font scales: the default, large, is Android's 1.0.
FONT_SCALES: Mapping[str, str] = dict(
    zip(
        TEXT_SIZES,
        ("0.85", "0.9", "0.95", "1.0", "1.15", "1.3", "1.5", "1.6", "1.7", "1.8", "1.9", "2.0"),
        strict=True,
    )
)
#: The animation scales "remove animations" turns to 0.
ANIMATIONS = ("animator_duration_scale", "transition_animation_scale", "window_animation_scale")
#: SystemUI's demo mode, for a status bar to show: 9:41, full signal and battery, no notifications.
DEMO = (
    ("enter",),
    ("clock", "-e", "hhmm", "0941"),
    ("battery", "-e", "level", "100", "-e", "plugged", "false"),
    ("network", "-e", "wifi", "show", "-e", "level", "4", "-e", "mobile", "show", "-e", "datatype", "none"),
    ("notifications", "-e", "visible", "false"),
)
DEMO_ACTION = "com.android.systemui.demo"
#: How long a launch may take to show its first screen.
LAUNCH_TIMEOUT_S = 30.0
#: What the settings command prints for a setting that was never set.
UNSET = "null"
#: How often a route moves the device.
ROUTE_STEP_S = 1.0
EARTH_RADIUS_M = 6_371_000.0
_EXTRA = re.compile(r"\A([A-Za-z_][A-Za-z0-9_.]*)=(.*)\Z", re.S)
#: The fields ``dumpsys package`` says of an installed app.
_APP_FIELD = re.compile(r"^\s*(versionName|versionCode|lastUpdateTime|codePath)=(\S+(?: \S+)?)", re.M)

SerialOf = Callable[[str], Awaitable[str]]


def launch_extras(args: Sequence[str]) -> list[str]:
    """Launch arguments as intent extras: each ``key=value`` a string extra. Refused when one is not that."""
    extras: list[str] = []
    for arg in args:
        found = _EXTRA.match(arg)
        if found is None:
            raise AdbError(
                f"{arg!r} is not an Android launch argument: each is key=value, given to the app as a string extra "
                "of its launch intent"
            )
        extras += ["--es", found.group(1), found.group(2)]
    return extras


def nearest_text_size(scale: str) -> str | None:
    """The text size category a font scale is nearest; None for a scale that is not one."""
    try:
        value = float(scale)
    except ValueError:
        return None
    return min(TEXT_SIZES, key=lambda size: abs(float(FONT_SCALES[size]) - value))


def _distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """The great-circle distance between two places, in metres."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def route_fixes(waypoints: Sequence[tuple[float, float]], speed: float) -> list[tuple[float, float]]:
    """Where a device moving along waypoints at `speed` metres a second is each second: every waypoint, and the
    places between them, the last one last."""
    fixes = [waypoints[0]]
    for start, end in itertools.pairwise(waypoints):
        steps = max(1, math.ceil(_distance_m(start, end) / max(speed, 0.1) / ROUTE_STEP_S))
        fixes += [
            (start[0] + (end[0] - start[0]) * step / steps, start[1] + (end[1] - start[1]) * step / steps)
            for step in range(1, steps + 1)
        ]
    return fixes


class AndroidControl:
    """`DeviceControl` and `ExactRestore` for one Android device's tool, adb, reaching the device by its id."""

    def __init__(
        self,
        adb: Adb,
        serial_of: SerialOf,
        *,
        routes: MutableMapping[str, asyncio.Task[None]] | None = None,
        console_for: Callable[[int], EmulatorConsole] = EmulatorConsole,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.adb = adb
        self._serial_of = serial_of
        #: The routes being driven, by device: shared by every control the device's backend hands out.
        self._routes = routes if routes is not None else {}
        self._console_for = console_for
        self._sleep = sleep

    async def _run(self, udid: str, command: Sequence[str], *, timeout: float | None = None) -> str:
        return await self.adb.run(await self._serial_of(udid), command, timeout=timeout)

    async def _setting(self, udid: str, namespace: str, key: str) -> str:
        return (await self._run(udid, ["settings", "get", namespace, key])).strip()

    async def _set(self, udid: str, namespace: str, key: str, value: str) -> None:
        if value == UNSET:
            await self._run(udid, ["settings", "delete", namespace, key])
        else:
            await self._run(udid, ["settings", "put", namespace, key, value])

    # -- apps --------------------------------------------------------------------------------------------------------

    async def install(self, udid: str, app_path: str) -> None:
        path = Path(app_path)
        apks = sorted(path.glob("*.apk")) if path.is_dir() else [path] if path.is_file() else []
        if not apks:
            raise AdbError(f"{path} holds no APK to install")
        await self.adb.install(await self._serial_of(udid), apks)

    async def app_info(self, udid: str, bundle_id: str) -> InstalledApp | None:
        said = await self._run(udid, ["dumpsys", "package", bundle_id])
        fields: dict[str, str] = {}
        # The package's own section comes first; an overlay of it may follow with fields of its own.
        for key, value in _APP_FIELD.findall(said):
            # ``versionCode=36 minSdk=36 targetSdk=36``: the code is the first word.
            fields.setdefault(key, value.split(" ")[0] if key == "versionCode" else value)
        if "versionName" not in fields and "codePath" not in fields:
            return None
        name, code = fields.get("versionName", ""), fields.get("versionCode", "")
        version = f"{name} ({code})" if name and code and name != code else name or code
        info: dict[str, Any] = {
            key: fields[key] for key in ("versionName", "versionCode", "lastUpdateTime") if key in fields
        }
        return InstalledApp(bundle_id, bundle_id.rsplit(".", 1)[-1], version, fields.get("codePath", ""), info)

    async def _launcher(self, udid: str, bundle_id: str) -> str:
        said = await self._run(
            udid, ["cmd", "package", "resolve-activity", "--brief", "-c", "android.intent.category.LAUNCHER", bundle_id]
        )
        component = said.strip().splitlines()[-1] if said.strip() else ""
        if "/" not in component:
            raise AdbError(f"{bundle_id} has no activity to launch: is it installed, and does it have a launcher icon?")
        return component

    async def _pid(self, udid: str, bundle_id: str) -> int | None:
        said = (await self.adb.shell(await self._serial_of(udid), ["pidof", bundle_id])).text.split()
        return int(said[0]) if said and said[0].isdigit() else None

    async def launch(
        self, udid: str, bundle_id: str, args: Sequence[str] = (), *, terminate_running: bool = False
    ) -> int | None:
        extras = launch_extras(args)
        component = await self._launcher(udid, bundle_id)
        if terminate_running:
            await self._run(udid, ["am", "force-stop", bundle_id])
        said = await self._run(udid, ["am", "start", "-W", "-n", component, *extras], timeout=LAUNCH_TIMEOUT_S)
        _started(said, bundle_id)
        return await self._pid(udid, bundle_id)

    async def terminate(self, udid: str, bundle_id: str) -> bool:
        running = await self._pid(udid, bundle_id) is not None
        await self._run(udid, ["am", "force-stop", bundle_id])
        return running

    async def openurl(self, udid: str, url: str) -> None:
        said = await self._run(udid, ["am", "start", "-W", "-a", "android.intent.action.VIEW", "-d", url])
        _started(said, url)

    async def pbcopy(self, udid: str, text: str) -> None:
        raise AdbError("An Android device's clipboard is set by typing, which types text whole: nothing is pasted.")

    async def logs(self, udid: str, *, since_s: int, bundle_id: str | None) -> list[str]:
        serial = await self._serial_of(udid)
        now = (await self.adb.run(serial, ["date", "+%s"])).strip()
        since = f"{int(now) - since_s}.000" if now.isdigit() else ""
        window = ["-T", since] if since else []
        if bundle_id is None:
            said = await self.adb.run(serial, ["logcat", "-d", "-v", "year", *window, "*:E"])
            return _log_lines(said)
        uid = await self._uid(serial, bundle_id)
        said = await self.adb.run(serial, ["logcat", "-d", "-v", "year", "-v", "uid", *window])
        return [line for line in _log_lines(said) if line.split()[2:3] == [uid]]

    async def _uid(self, serial: str, bundle_id: str) -> str:
        listed = await self.adb.run(serial, ["pm", "list", "packages", "-U", bundle_id])
        for line in listed.splitlines():
            name, _, uid = line.removeprefix("package:").partition(" uid:")
            if name == bundle_id and uid.strip().isdigit():
                return uid.strip()
        raise AdbError(f"{bundle_id} is not installed on the device")

    # -- how it looks ------------------------------------------------------------------------------------------------

    async def display(self, udid: str) -> DisplayState:
        night = await self._night(udid)
        scale = await self._setting(udid, "system", "font_scale")
        contrast = await self._setting(udid, "secure", "high_text_contrast_enabled")
        animations = await self._setting(udid, "global", ANIMATIONS[0])
        return DisplayState(
            appearance={"yes": "dark", "no": "light"}.get(night),
            text_size=nearest_text_size(scale if scale != UNSET else "1.0"),
            contrast=contrast == "1",
            reduce_motion=animations in ("0", "0.0"),
        )

    async def _night(self, udid: str) -> str:
        said = await self._run(udid, ["cmd", "uimode", "night"])
        return said.strip().rpartition(":")[2].strip()

    async def appearance(self, udid: str, mode: str) -> None:
        await self._run(udid, ["cmd", "uimode", "night", "yes" if mode == "dark" else "no"])

    async def text_size(self, udid: str, size: str) -> None:
        scale = FONT_SCALES.get(size)
        if scale is None:
            raise AdbError(f"{size} is not a text size; one of {', '.join(TEXT_SIZES)}")
        await self._set(udid, "system", "font_scale", scale)

    async def contrast(self, udid: str, on: bool) -> None:
        await self._set(udid, "secure", "high_text_contrast_enabled", "1" if on else "0")

    async def reduce_motion(self, udid: str, on: bool) -> None:
        for key in ANIMATIONS:
            await self._set(udid, "global", key, "0" if on else "1")

    async def demo_status_bar(self, udid: str) -> None:
        await self._set(udid, "global", "sysui_demo_allowed", "1")
        for command in DEMO:
            await self._demo(udid, command)

    async def clear_status_bar(self, udid: str) -> None:
        await self._demo(udid, ("exit",))

    async def _demo(self, udid: str, command: Sequence[str]) -> None:
        await self._run(udid, ["am", "broadcast", "-a", DEMO_ACTION, "-e", "command", *command])

    async def rotation(self, udid: str) -> int:
        """Which way the screen is turned: 0 upright, then each quarter turn anticlockwise."""
        said = await self._setting(udid, "system", "user_rotation")
        return int(said) if said.isdigit() else 0

    async def set_rotation(self, udid: str, quarter: int) -> None:
        """Turn the screen, and keep it turned: the device stops following its accelerometer."""
        await self._set(udid, "system", "accelerometer_rotation", "0")
        await self._set(udid, "system", "user_rotation", str(quarter % 4))

    # -- what each change replaces, exactly (`ExactRestore`) ---------------------------------------------------------

    async def saved(self, udid: str, what: str) -> Any:
        if what == "appearance":
            return await self._night(udid)
        if what == "text_size":
            return await self._setting(udid, "system", "font_scale")
        if what == "contrast":
            return await self._setting(udid, "secure", "high_text_contrast_enabled")
        if what == "reduce_motion":
            return {key: await self._setting(udid, "global", key) for key in ANIMATIONS}
        if what == "status_bar":
            return await self._setting(udid, "global", "sysui_demo_allowed")
        if what == "rotation":
            return {
                key: await self._setting(udid, "system", key) for key in ("accelerometer_rotation", "user_rotation")
            }
        raise AdbError(f"{what} is not something SimMirror changes on an Android device")

    async def put_back(self, udid: str, what: str, saved: Any) -> None:
        if what == "appearance":
            await self._run(udid, ["cmd", "uimode", "night", str(saved)])
        elif what == "text_size":
            await self._set(udid, "system", "font_scale", str(saved))
        elif what == "contrast":
            await self._set(udid, "secure", "high_text_contrast_enabled", str(saved))
        elif what == "status_bar":
            await self.clear_status_bar(udid)
            await self._set(udid, "global", "sysui_demo_allowed", str(saved))
        elif what in ("reduce_motion", "rotation") and isinstance(saved, dict):
            namespace = "global" if what == "reduce_motion" else "system"
            # Rotation's user_rotation before accelerometer_rotation, so a screen that followed the device does again.
            for key, value in sorted(saved.items(), key=lambda item: item[0] == "accelerometer_rotation"):
                await self._set(udid, namespace, str(key), str(value))
        else:
            raise AdbError(f"{what} cannot be put back on an Android device from {saved!r}")

    # -- where it is ---------------------------------------------------------------------------------------------------

    async def _console(self, udid: str) -> EmulatorConsole:
        port = port_of(await self._serial_of(udid))
        if port is None:
            raise AdbError(
                "A phone's location cannot be set through adb: only an emulator's can, through its console. Set it "
                "with an app that mocks locations, chosen in the phone's Developer options."
            )
        return self._console_for(port)

    async def locate(self, udid: str, latitude: float, longitude: float) -> None:
        self._stop_route(udid)
        await (await self._console(udid)).geo_fix(latitude, longitude)

    async def route(self, udid: str, waypoints: Sequence[tuple[float, float]], speed: float) -> None:
        console = await self._console(udid)
        self._stop_route(udid)
        fixes = route_fixes(waypoints, speed)
        await console.geo_fix(*fixes[0])

        async def drive() -> None:
            for fix in fixes[1:]:
                await self._sleep(ROUTE_STEP_S)
                await console.geo_fix(*fix)

        self._routes[udid] = asyncio.get_running_loop().create_task(drive())

    async def clear_location(self, udid: str) -> None:
        """Stop a route: the emulator keeps the last place it was given, as its console has no way to forget one."""
        self._stop_route(udid)

    def _stop_route(self, udid: str) -> None:
        task = self._routes.pop(udid, None)
        if task is not None:
            task.cancel()


def _started(said: str, what: str) -> None:
    """An ``am start`` that says why it did not start is refused with that. One that brought a running app's task to
    the front -- ``Warning: Activity not started, its current task has been brought to the front`` -- started it."""
    errors = [line for line in said.splitlines() if line.startswith("Error")]
    if errors:
        last = errors[-1]
        raise AdbError(f"{what} did not start: {last.partition(':')[2].strip() or last}")


def _log_lines(said: str) -> list[str]:
    return [line for line in said.splitlines() if line.strip() and not line.startswith("--------- beginning of")]


async def stop_routes(routes: MutableMapping[str, asyncio.Task[None]]) -> None:
    """End every route being driven: the backend is let go."""
    for task in list(routes.values()):
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    routes.clear()
