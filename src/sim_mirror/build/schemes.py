# SPDX-License-Identifier: Apache-2.0
"""The build configuration a scheme's own action builds with, read from its `.xcscheme` -- as Xcode does on Run.

A scheme's Run action may build another configuration than Debug, and many projects give each configuration its own
bundle id: a build in Debug then installs a second app beside the one a person uses, with its own data. So a run that
names no configuration takes the one the scheme's Run action (``LaunchAction``) names for a build, and its Test action
(``TestAction``) for tests.

**Where a scheme is**: in the project's -- or the workspace's -- ``xcshareddata/xcschemes``, where a team keeps the
schemes it shares, or in the person's own ``xcuserdata/<user>.xcuserdatad/xcschemes``; a workspace's schemes are also
its projects'. Another person's `xcuserdata`, committed by mistake, is theirs: Xcode does not read it, so neither is it
read here. A scheme Xcode makes up for a project, with no file anywhere, builds Debug.

**How it is read**: with the standard library's expat, never fetching anything and refusing any entity declaration,
as `plistlib` does -- a scheme file has none, so one that declares one is not read at all. A file longer than any
scheme is not read either, and a name the file gives is used only when a call could have given it (`is_xcode_name`).
"""

from __future__ import annotations

import getpass
from collections.abc import Sequence
from pathlib import Path
from xml.parsers import expat

from sim_mirror.validation import is_xcode_name

#: The scheme action whose configuration each kind of run builds with.
ACTIONS = {"build": "LaunchAction", "test": "TestAction"}
#: The configuration a run builds with when neither the call, the settings nor a scheme names one: Xcode's for a
#: scheme it makes up.
DEFAULT_CONFIGURATION = "Debug"
#: Longer than any scheme Xcode writes; a bigger file is not read.
SCHEME_MAX_BYTES = 1_000_000


class _NotAScheme(Exception):
    """A file that is no scheme Xcode writes: another document, or one that declares an entity."""


def scheme_files(containers: Sequence[Path], scheme: str, user: str | None = None) -> list[Path]:
    """Where a scheme of this name may be, most likely first: each container's shared schemes, then the person's own.

    `containers` is the project, or the workspace and then each project it names.
    """
    if "/" in scheme or scheme.startswith("."):
        return []
    owner = user if user is not None else getpass.getuser()
    name = f"{scheme}.xcscheme"
    return [
        place
        for container in containers
        for place in (
            container / "xcshareddata" / "xcschemes" / name,
            container / "xcuserdata" / f"{owner}.xcuserdatad" / "xcschemes" / name,
        )
    ]


def action_configuration(document: bytes, action: str) -> str | None:
    """The configuration a scheme's `action` builds with, or None when the scheme does not say, or cannot be read."""
    found: list[str] = []
    depth = 0

    def start(name: str, attributes: dict[str, str]) -> None:
        nonlocal depth
        depth += 1
        if depth == 1 and name != "Scheme":
            raise _NotAScheme
        if depth == 2 and name == action and not found:
            found.append(attributes.get("buildConfiguration", ""))

    def end(_: str) -> None:
        nonlocal depth
        depth -= 1

    def declared(*_: object) -> None:
        raise _NotAScheme

    parser = expat.ParserCreate()
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.EntityDeclHandler = declared
    try:
        parser.Parse(document, True)
    except (expat.ExpatError, _NotAScheme):
        return None
    return found[0] if found and is_xcode_name(found[0]) else None


def scheme_configuration(places: Sequence[Path], kind: str) -> str | None:
    """The configuration the first scheme file there is names for this kind of run, or None when there is none."""
    for place in places:
        try:
            if not place.is_file() or place.stat().st_size > SCHEME_MAX_BYTES:
                continue
            document = place.read_bytes()
        except OSError:
            continue
        return action_configuration(document, ACTIONS[kind])
    return None
