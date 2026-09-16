# SPDX-License-Identifier: Apache-2.0
"""Which Xcode a program SimMirror starts runs with, and how that program is told.

Three things can name an Xcode, and the first that does wins:

1. the scope's ``device.developer_dir`` setting;
2. a ``DEVELOPER_DIR`` SimMirror was itself started with, which every program it starts inherits;
3. ``xcode-select``, which is the whole machine's and changes under a running daemon when a person switches it.

A program is told by ``DEVELOPER_DIR`` in its environment, never by ``xcode-select``: a Mac with two Xcodes is usually
one where a person keeps the older one selected for other work.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from sim_mirror.platform import process
from sim_mirror.platform.process import Runner

DEVELOPER_DIR = "DEVELOPER_DIR"

Source = Literal["setting", "environment", "xcode-select"]

#: How each source is named to a person, so a report says where to change it.
SOURCE_NAMES: Mapping[Source, str] = {
    "setting": "device.developer_dir",
    "environment": DEVELOPER_DIR,
    "xcode-select": "xcode-select",
}


@dataclass(frozen=True)
class ChosenXcode:
    """An Xcode's developer folder, and what named it."""

    path: str
    source: Source

    def __str__(self) -> str:
        return f"{self.path} ({SOURCE_NAMES[self.source]})"


def developer_env(developer_dir: str = "", base: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment a child runs with: this process's (or `base`), pointed at `developer_dir` when one is given."""
    env = dict(os.environ if base is None else base)
    if developer_dir:
        env[DEVELOPER_DIR] = developer_dir
    return env


async def selected_developer_dir(run: Runner = process.run) -> str | None:
    """The developer folder ``xcode-select`` names, or None when none is selected."""
    code, out = await run(("xcode-select", "-p"))
    path = out.strip()
    return path if code == 0 and path else None


async def choose_xcode(
    configured: str, env: Mapping[str, str] | None = None, run: Runner = process.run
) -> ChosenXcode | None:
    """The Xcode a child started now runs with, or None when nothing names one."""
    if configured:
        return ChosenXcode(configured, "setting")
    inherited = (os.environ if env is None else env).get(DEVELOPER_DIR, "").strip()
    if inherited:
        return ChosenXcode(inherited, "environment")
    selected = await selected_developer_dir(run)
    return ChosenXcode(selected, "xcode-select") if selected else None
