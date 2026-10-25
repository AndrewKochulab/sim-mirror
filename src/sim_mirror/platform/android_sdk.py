# SPDX-License-Identifier: Apache-2.0
"""The Android SDK on this Mac, and the virtual devices (AVDs) made with it -- read from their files, never run.

**The SDK** is the folder ``android.sdk_path`` names, else ``$ANDROID_HOME``, else ``$ANDROID_SDK_ROOT`` (the older
name), else Android Studio's own, ``~/Library/Android/sdk``. adb is in its ``platform-tools`` and the emulator in its
``emulator`` folder, each with a ``source.properties`` saying which version it is.

**An AVD** is an ``<name>.ini`` in the AVD folder -- ``$ANDROID_AVD_HOME``, else ``$ANDROID_EMULATOR_HOME/avd``, else
``~/.android/avd`` -- pointing at its own ``<name>.avd`` folder, whose ``config.ini`` says what it is: the name a person
gave it, its screen, and the system image it runs, as a folder of the SDK. An AVD whose system image is gone, or only
half there -- Android Studio can leave one without its ``system.img`` -- cannot start, and says so (`Avd.problem`)
rather than failing a minute into a boot.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

SDK_ENVS = ("ANDROID_HOME", "ANDROID_SDK_ROOT")
AVD_HOME_ENV = "ANDROID_AVD_HOME"
EMULATOR_HOME_ENV = "ANDROID_EMULATOR_HOME"
#: Where Android Studio puts the SDK on a Mac.
STUDIO_SDK = Path("Library/Android/sdk")
#: What a system image needs to boot: without them the emulator stops with "No initial system image".
IMAGE_FILES = ("system.img", "vendor.img")
_API = re.compile(r"android-(\d+)")


def read_properties(path: Path) -> dict[str, str]:
    """A ``key=value`` file -- an AVD's ``.ini``, a package's ``source.properties`` -- or nothing if unreadable."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    found = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.lstrip().startswith("#"):
            found[key.strip()] = value.strip()
    return found


def _version(folder: Path) -> str | None:
    return read_properties(folder / "source.properties").get("Pkg.Revision")


@dataclass(frozen=True)
class AndroidSdk:
    """An Android SDK folder, and the tools SimMirror runs from it."""

    root: Path

    @property
    def adb(self) -> Path:
        return self.root / "platform-tools" / "adb"

    @property
    def emulator(self) -> Path:
        return self.root / "emulator" / "emulator"

    @property
    def adb_version(self) -> str | None:
        """adb's version, as its package says it; None when adb is not installed."""
        return _version(self.adb.parent) if self.adb.is_file() else None

    @property
    def emulator_version(self) -> str | None:
        """The emulator's version, as its package says it; None when the emulator is not installed."""
        return _version(self.emulator.parent) if self.emulator.is_file() else None


def find_sdk(configured: str = "", env: Mapping[str, str] = os.environ, home: Path | None = None) -> AndroidSdk | None:
    """The SDK these settings and this environment name, or Android Studio's -- None when there is no folder there."""
    named = [configured, *(env.get(name, "") for name in SDK_ENVS)]
    for value in named:
        if value:
            path = Path(value).expanduser()
            return AndroidSdk(path) if path.is_dir() else None
    studio = (home or Path.home()) / STUDIO_SDK
    return AndroidSdk(studio) if studio.is_dir() else None


def avd_home(env: Mapping[str, str] = os.environ, home: Path | None = None) -> Path:
    """The folder AVDs are listed in."""
    if env.get(AVD_HOME_ENV):
        return Path(env[AVD_HOME_ENV]).expanduser()
    if env.get(EMULATOR_HOME_ENV):
        return Path(env[EMULATOR_HOME_ENV]).expanduser() / "avd"
    return (home or Path.home()) / ".android" / "avd"


@dataclass(frozen=True)
class Avd:
    """An Android virtual device, as its files describe it."""

    #: The name the emulator starts it by: ``Pixel_9``.
    name: str
    #: What a person called it: ``Pixel 9``.
    display_name: str
    #: The Android API level its system image is: 36.
    api: int | None
    width_px: int | None
    height_px: int | None
    density: int | None
    #: The system image it runs, a folder of the SDK; None when its configuration names none.
    image: Path | None
    #: Why it cannot start; None when it can.
    problem: str | None = None


def _whole(value: str | None) -> int | None:
    return int(value) if value and value.isdigit() else None


def _image(config: Mapping[str, str], sdk: AndroidSdk | None) -> Path | None:
    named = config.get("image.sysdir.1")
    if not named or sdk is None:
        return None
    return (sdk.root / named).resolve()


def _problem(folder: Path, image: Path | None, sdk: AndroidSdk | None) -> str | None:
    if not folder.is_dir():
        return f"its folder {folder} is gone"
    if sdk is None:
        return "no Android SDK was found to run it"
    if image is None or not image.is_dir():
        return "its system image is not installed: install it in Android Studio's SDK Manager"
    missing = [file for file in IMAGE_FILES if not (image / file).is_file()]
    if missing:
        return (
            f"its system image is incomplete ({', '.join(missing)} missing): install it again in Android Studio's "
            "SDK Manager"
        )
    return None


def read_avd(ini: Path, sdk: AndroidSdk | None) -> Avd:
    """The AVD an ``<name>.ini`` names."""
    name = ini.stem
    pointer = read_properties(ini)
    folder = Path(pointer.get("path") or ini.with_suffix(".avd"))
    config = read_properties(folder / "config.ini")
    image = _image(config, sdk)
    api = _API.search(config.get("image.sysdir.1", "") or pointer.get("target", ""))
    return Avd(
        name=name,
        display_name=config.get("avd.ini.displayname") or name.replace("_", " "),
        api=int(api.group(1)) if api else None,
        width_px=_whole(config.get("hw.lcd.width")),
        height_px=_whole(config.get("hw.lcd.height")),
        density=_whole(config.get("hw.lcd.density")),
        image=image,
        problem=_problem(folder, image, sdk),
    )


def avds(sdk: AndroidSdk | None, env: Mapping[str, str] = os.environ, home: Path | None = None) -> list[Avd]:
    """Every AVD on this Mac, by name."""
    return [read_avd(ini, sdk) for ini in sorted(avd_home(env, home).glob("*.ini")) if ini.is_file()]
