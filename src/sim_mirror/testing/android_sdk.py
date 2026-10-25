# SPDX-License-Identifier: Apache-2.0
"""An Android SDK and its AVDs on disk, for tests: the folders and files SimMirror reads, and nothing it would run."""

from __future__ import annotations

from pathlib import Path

from sim_mirror.platform.android_sdk import AndroidSdk


def sdk_at(root: Path, *, adb: bool = True, emulator: bool = True) -> AndroidSdk:
    """An SDK at `root`: adb 37.0.0 and the emulator 36.4.9, each as its package says, unless left out."""
    for wanted, folder, binary, version in (
        (adb, "platform-tools", "adb", "37.0.0"),
        (emulator, "emulator", "emulator", "36.4.9"),
    ):
        if wanted:
            (root / folder).mkdir(parents=True)
            (root / folder / binary).write_text("#!/bin/sh\n")
            (root / folder / "source.properties").write_text(f"Pkg.Desc=x\nPkg.Revision={version}\n")
    root.mkdir(parents=True, exist_ok=True)
    return AndroidSdk(root)


def image(sdk: AndroidSdk, api: str, *, whole: bool = True) -> str:
    """A system image in the SDK, as an AVD's configuration names it -- half there unless `whole`."""
    named = f"system-images/android-{api}/google_apis/arm64-v8a/"
    folder = sdk.root / named
    folder.mkdir(parents=True)
    (folder / "kernel-ranchu").write_text("")
    if whole:
        (folder / "system.img").write_text("")
        (folder / "vendor.img").write_text("")
    return named


def avd(home: Path, name: str, config: str, *, folder: Path | None = None, target: str = "android-36") -> None:
    """An AVD in the AVD folder `home`: its ``.ini``, and its own folder's ``config.ini``."""
    home.mkdir(parents=True, exist_ok=True)
    made = folder or home / f"{name}.avd"
    made.mkdir(parents=True, exist_ok=True)
    (home / f"{name}.ini").write_text(f"avd.ini.encoding=UTF-8\npath={made}\ntarget={target}\n")
    (made / "config.ini").write_text(config)
