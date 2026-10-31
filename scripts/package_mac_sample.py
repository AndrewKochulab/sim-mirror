# SPDX-License-Identifier: Apache-2.0
"""Bundle the built sample executable so LaunchServices and SDK discovery see a real application."""

from __future__ import annotations

import plistlib
import shutil
import sys
from pathlib import Path


def package(binary: Path, app: Path, name: str = "MacAppSDK", bundle_id: str = "dev.simmirror.MacAppSDK") -> None:
    contents = app / "Contents"
    (contents / "MacOS").mkdir(parents=True, exist_ok=True)
    shutil.copy2(binary, contents / "MacOS" / name)
    (contents / "Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": bundle_id,
                "CFBundleName": name,
                "CFBundleExecutable": name,
                "CFBundlePackageType": "APPL",
                "CFBundleShortVersionString": "1.0",
                "LSMinimumSystemVersion": "14.0",
                "NSHighResolutionCapable": True,
            }
        )
    )


if __name__ == "__main__":
    package(Path(sys.argv[1]), Path(sys.argv[2]), *sys.argv[3:])
