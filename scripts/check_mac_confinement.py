# SPDX-License-Identifier: Apache-2.0
"""Keep the app-scoped framework adapters free of global input, activation and display capture.

The iOS helper has separate simulator transports. This guard checks only the macOS adapters and sample so a future
fallback cannot silently widen their reach when an app refuses an accessibility or local responder action.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from _repo import REPO_ROOT

FOLDERS = ("sdk/swift/Sources/SimMirrorKit/AppKit", "examples/mac-app-sdk/Sources")
PATTERN = re.compile(
    r"\b(?:CGEventPost|CGEventPostToPid|CGWarpMouseCursorPosition|CGAssociateMouseAndMouseCursorPosition|"
    r"AXUIElementCreateSystemWide)\s*\(|\.\s*(?:postToPid|makeKeyAndOrderFront)\s*\(|"
    r"(?<!NSLayoutConstraint)\.\s*activate\s*\(|"
    r"\.post\s*\(\s*tap:|SCContentFilter\s*\(\s*display:"
)


def offenders(root: Path) -> list[str]:
    files = list((root / "helper/Sources/HelperPlatform").glob("Mac*.swift"))
    files += [path for folder in FOLDERS for path in (root / folder).glob("*.swift")]
    return [
        f"{path.relative_to(root)}:{number}: {line.strip()}"
        for path in files
        for number, line in enumerate(path.read_text().splitlines(), 1)
        if PATTERN.search(line)
    ]


def main(root: Path = REPO_ROOT) -> int:
    found = offenders(root)
    if found:
        print(
            "app-scoped code must not reach global input, activation or display capture:\n" + "\n".join(found),
            file=sys.stderr,
        )
        return 1
    print("macOS confinement ok: app adapters use no global input, activation or display capture")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
