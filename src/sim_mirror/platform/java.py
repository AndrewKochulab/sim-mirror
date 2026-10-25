# SPDX-License-Identifier: Apache-2.0
"""The JDK Gradle builds an Android app with.

The one ``android.java_home`` names, else ``$JAVA_HOME``, else Android Studio's own (its ``jbr``, the JDK Android
Studio builds with), else the one macOS's ``/usr/libexec/java_home`` picks. A JDK is a folder with ``bin/java`` in it;
its ``release`` file says which version it is, which `sim-mirror doctor` shows. Nothing here runs Java itself.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from sim_mirror.platform.process import Runner, run

JAVA_HOME_ENV = "JAVA_HOME"
#: Where Android Studio keeps its JDK, in /Applications or the person's own Applications folder.
STUDIO_JDK = Path("Android Studio.app/Contents/jbr/Contents/Home")
#: macOS's own finder of installed JDKs.
JAVA_HOME_TOOL = "/usr/libexec/java_home"
_VERSION = re.compile(r'^JAVA_VERSION="([^"]+)"', re.M)


@dataclass(frozen=True)
class Jdk:
    """A JDK on this Mac, and where it was found."""

    home: Path
    #: What named it: ``android.java_home``, ``$JAVA_HOME``, Android Studio, or macOS.
    source: str

    @property
    def version(self) -> str | None:
        """The version its ``release`` file says, or None when it says none."""
        try:
            found = _VERSION.search((self.home / "release").read_text(encoding="utf-8", errors="replace"))
        except OSError:
            return None
        return found.group(1) if found else None


def _is_jdk(path: Path) -> bool:
    return (path / "bin" / "java").is_file()


async def find_jdk(
    configured: str = "",
    env: Mapping[str, str] = os.environ,
    home: Path | None = None,
    *,
    runner: Runner = run,
    applications: Path = Path("/Applications"),
) -> Jdk | None:
    """The JDK these settings and this environment name, or Android Studio's, or macOS's -- None when there is none.

    A folder the settings or ``$JAVA_HOME`` name that is not a JDK is that answer, None: a build with another JDK than
    the one a person named would not be the build they asked for.
    """
    for value, source in ((configured, "android.java_home"), (env.get(JAVA_HOME_ENV, ""), f"${JAVA_HOME_ENV}")):
        if value:
            path = Path(value).expanduser()
            return Jdk(path, source) if _is_jdk(path) else None
    for folder in (applications, (home or Path.home()) / "Applications"):
        if _is_jdk(folder / STUDIO_JDK):
            return Jdk(folder / STUDIO_JDK, "Android Studio")
    rc, out = await runner([JAVA_HOME_TOOL])
    found = Path(out.strip()) if rc == 0 and out.strip() else None
    return Jdk(found, "macOS") if found is not None and _is_jdk(found) else None
