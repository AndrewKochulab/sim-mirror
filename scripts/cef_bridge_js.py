#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Embed sdk/web/bridge.js into the CEF adapter as a C++ header, byte for byte.

`--check` exits non-zero when the committed header no longer matches the script.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "sdk" / "web" / "bridge.js"
HEADER = ROOT / "sdk" / "cef" / "include" / "sim_mirror_cef" / "bridge_js.h"
DELIMITER = "SIMMIRRORJS"


def render() -> str:
    script = SCRIPT.read_text(encoding="utf-8")
    if f"){DELIMITER}" in script:
        raise SystemExit("bridge.js contains the raw-string delimiter")
    return (
        "// SPDX-License-Identifier: Apache-2.0\n"
        "// Generated from sdk/web/bridge.js by scripts/cef_bridge_js.py; do not edit.\n"
        "#pragma once\n\n"
        "#if defined(SIM_MIRROR_CEF_BRIDGE) && SIM_MIRROR_CEF_BRIDGE\n"
        "namespace sim_mirror_cef {\n"
        f'inline constexpr const char* kBridgeScript = R"{DELIMITER}({script}){DELIMITER}";\n'
        "}  // namespace sim_mirror_cef\n"
        "#endif  // SIM_MIRROR_CEF_BRIDGE\n"
    )


def main(argv: list[str]) -> int:
    rendered = render()
    if "--check" in argv:
        if not HEADER.exists() or HEADER.read_text(encoding="utf-8") != rendered:
            print(f"{HEADER.relative_to(ROOT)} is stale: run scripts/cef_bridge_js.py", file=sys.stderr)
            return 1
        return 0
    HEADER.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
