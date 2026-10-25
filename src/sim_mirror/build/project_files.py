# SPDX-License-Identifier: Apache-2.0
"""A project's own files, found by name: where a failure that names only a file -- ``TripTests.swift:6``,
``MainActivityTest.kt:36`` -- or the end of its path -- ``layout/main.xml:26`` -- is in the folder built.

Build output, dependencies and hidden folders are left out of the walk, which keeps a project's folder small. A name
several files match is left as it came, since picking one could send the agent to the wrong file.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Collection, Iterator
from pathlib import Path

#: Folders a project's own sources and settings are never in, left out when looking through the project folder.
NOT_SOURCES = frozenset({"DerivedData", "build", "node_modules"})


def project_files(folder: Path) -> Iterator[tuple[str, list[str]]]:
    """Each folder under ``folder`` a project's own files can be in, and its files."""
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(name for name in dirs if name not in NOT_SOURCES and not name.startswith("."))
        yield root, sorted(files)


def _found(folder: Path, names: Collection[str], matches: Callable[[str, str, str], bool]) -> dict[str, str]:
    """Each name the one file in the project `matches` (name, file name, path relative to the folder) says it is."""
    found: dict[str, list[str]] = {name: [] for name in names}
    for root, files in project_files(folder) if found else ():
        for file in files:
            path = os.path.relpath(os.path.join(root, file), folder)
            for name, paths in found.items():
                if matches(name, file, path):
                    paths.append(path)
    return {name: paths[0] for name, paths in found.items() if len(paths) == 1}


def source_paths(folder: Path, names: Collection[str]) -> dict[str, str]:
    """Where each bare file name a test failure gives is in the project, relative to its folder.

    A failure on Xcode 26.6 says only ``TripTests.swift:6``, which an agent would then have to go and find (27.0 gives
    the whole path).
    """
    bare = [name for name in names if name and "/" not in name]
    return _found(folder, bare, lambda name, file, path: name == file)


def ending_paths(folder: Path, names: Collection[str]) -> dict[str, str]:
    """Where each path's end a failure gives -- ``layout/main.xml``, as Android's resource linker names a file -- is
    in the project, relative to its folder."""
    tails = [name for name in names if name and not name.startswith("/")]
    return _found(folder, tails, lambda name, file, path: f"/{path}".endswith(f"/{name}"))
