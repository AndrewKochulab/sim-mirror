# SPDX-License-Identifier: Apache-2.0
"""The wheel's build hook: the native helper's Swift package, and the helper itself when a release built one.

Every wheel carries the helper's sources at ``sim_mirror/_helper_src``, so ``sim-mirror helper build`` works from any
install. A release sets `BINARY_ENV` to the universal helper it built and signed; the wheel then also carries it at
``sim_mirror/_bin/sim-mirror-helper`` and, holding a Mac binary, is tagged for macOS rather than any platform. A wheel
built without it is the pure one, which falls back to building the helper on first use.

Hatchling loads this file by path (``[tool.hatch.build.targets.wheel.hooks.custom]``); only `HelperBuildHook` needs it.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NamedTuple

BINARY_ENV = "SIM_MIRROR_HELPER_BINARY"
PROGRAM = "sim-mirror-helper"
HELPER = "helper"
SOURCES = ("Package.swift", "Sources", "Tests")
SOURCES_TARGET = "sim_mirror/_helper_src"
BINARY_TARGET = f"sim_mirror/_bin/{PROGRAM}"
#: The helper is built for macOS 14 or later, for both Mac architectures (`make helper-build`).
MAC_TAG = "py3-none-macosx_14_0_universal2"


class HelperBinaryMissing(Exception):
    """`BINARY_ENV` names a helper that is not an executable file."""


# A NamedTuple, not a dataclass: hatchling loads this file without registering it as a module, which dataclasses need.
class WheelHelper(NamedTuple):
    """What the hook adds to a wheel: each source or built path → its path in the wheel, and the wheel's tag if new."""

    force_include: dict[str, str]
    tag: str | None = None


def wheel_helper(root: Path, env: Mapping[str, str]) -> WheelHelper:
    """What a wheel built from `root` carries of the helper, given the build's environment."""
    helper = root / HELPER
    include = {str(helper / name): f"{SOURCES_TARGET}/{name}" for name in SOURCES if (helper / name).exists()}
    configured = env.get(BINARY_ENV, "").strip()
    if not configured:
        return WheelHelper(include)
    binary = Path(configured)
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise HelperBinaryMissing(f"{BINARY_ENV} names {binary}, which is not an executable helper")
    include[str(binary)] = BINARY_TARGET
    return WheelHelper(include, MAC_TAG)


def apply(build_data: dict[str, Any], helper: WheelHelper) -> None:
    """Record `helper` in hatchling's build data for a wheel."""
    build_data.setdefault("force_include", {}).update(helper.force_include)
    if helper.tag:
        build_data["tag"] = helper.tag
        build_data["pure_python"] = False


try:
    from hatchling.builders.hooks.plugin.interface import BuildHookInterface
except ImportError:  # the repository's checks import this file without hatchling
    pass
else:

    class HelperBuildHook(BuildHookInterface):  # type: ignore[misc]
        PLUGIN_NAME = "custom"

        def initialize(self, version: str, build_data: dict[str, Any]) -> None:
            if self.target_name == "wheel":
                apply(build_data, wheel_helper(Path(self.root), os.environ))
