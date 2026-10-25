# SPDX-License-Identifier: Apache-2.0
"""The JDK Gradle builds with: the one named, else Android Studio's, else macOS's."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from sim_mirror.platform.java import JAVA_HOME_TOOL, STUDIO_JDK, Jdk, find_jdk


def _jdk(folder: Path, version: str | None = "21.0.9") -> Path:
    (folder / "bin").mkdir(parents=True)
    (folder / "bin" / "java").touch()
    if version is not None:
        (folder / "release").write_text(f'IMPLEMENTOR="JetBrains"\nJAVA_VERSION="{version}"\n')
    return folder


class Asked:
    def __init__(self, rc: int = 1, out: str = "") -> None:
        self.rc, self.out = rc, out
        self.argv: list[Sequence[str]] = []

    async def __call__(self, argv: Sequence[str]) -> tuple[int, str]:
        self.argv.append(argv)
        return self.rc, self.out


async def test_the_jdk_named_is_the_one_or_none(tmp_path: Path) -> None:
    named = _jdk(tmp_path / "named")
    asked = Asked()
    found = await find_jdk(str(named), {}, tmp_path, runner=asked, applications=tmp_path)
    assert found == Jdk(named, "android.java_home") and found.version == "21.0.9"
    env = {"JAVA_HOME": str(named)}
    assert await find_jdk("", env, tmp_path, runner=asked, applications=tmp_path) == Jdk(named, "$JAVA_HOME")
    # A folder named that is not a JDK is not swapped for another: the build would not be the one asked for.
    _jdk(tmp_path / "Applications" / STUDIO_JDK)
    assert await find_jdk(str(tmp_path / "nothing"), {}, tmp_path, runner=asked, applications=tmp_path) is None
    assert asked.argv == []


async def test_android_studios_jdk_then_macos_is_found(tmp_path: Path) -> None:
    studio = _jdk(tmp_path / "home" / "Applications" / STUDIO_JDK, version=None)
    asked = Asked()
    found = await find_jdk("", {}, tmp_path / "home", runner=asked, applications=tmp_path / "apps")
    assert found == Jdk(studio, "Android Studio") and found.version is None
    zulu = _jdk(tmp_path / "zulu")
    (zulu / "release").write_text("nothing\n")
    asked = Asked(0, f"{zulu}\n")
    found = await find_jdk("", {}, tmp_path / "nobody", runner=asked, applications=tmp_path / "apps")
    assert found == Jdk(zulu, "macOS") and found.version is None and asked.argv == [[JAVA_HOME_TOOL]]
    assert await find_jdk("", {}, tmp_path / "nobody", runner=Asked(), applications=tmp_path / "apps") is None
    gone = Asked(0, f"{tmp_path / 'gone'}\n")
    assert await find_jdk("", {}, tmp_path / "nobody", runner=gone, applications=tmp_path / "apps") is None
