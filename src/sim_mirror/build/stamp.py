# SPDX-License-Identifier: Apache-2.0
"""The commit a build was made from, stamped into the app it installs, so the device can say which code it runs.

A build's answer names the source it built (`source`), but the app on the device says nothing of it: an app installed
earlier, by another build or by Xcode, looks the same. With ``build.stamp_source`` on, `sim_build_run` installs a copy
of the built app whose Info.plist also holds

    SIM_MIRROR_SOURCE_SHA     the commit, in full
    SIM_MIRROR_SOURCE_DIRTY   true when files differed from it

and `sim_app info` reads them back from the app installed, and compares them with the folder's checkout now.

**No project changes.** No build setting can add a key to an Info.plist the project does not name: the
``INFOPLIST_KEY_*`` settings cover only the keys Xcode knows, and ``INFOPLIST_PREPROCESS`` only expands macros the file
already holds. So the key is added after the build, to a copy -- a copy-on-write clone where the disk allows, made in an
instant -- never to the app in DerivedData: Xcode's next build neither sees nor undoes it, and nothing stale is left
there with a commit it was not built from.

**Signing.** An app's signature seals its Info.plist, so the copy is signed again (`platform.codesign`) -- ad hoc, as
Xcode signs an app for a simulator. A simulator only: a real device's app is signed for it by a team's certificate,
which SimMirror does not hold, so it is never stamped. A copy that cannot be stamped or signed is not installed; the
app is, as built, and the answer says why it carries no stamp.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import plistlib
import re
import shutil
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sim_mirror.build.source import SHORT, Source

SHA_KEY = "SIM_MIRROR_SOURCE_SHA"
DIRTY_KEY = "SIM_MIRROR_SOURCE_DIRTY"
#: The folder of a scope's own DerivedData the stamped copies are made in, beside the apps they copy.
STAMPED = "SimMirrorStamped"
#: A commit: SHA-1 or SHA-256.
_COMMIT = re.compile(r"\A(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")

#: Signs a stamped copy again; None when it did, else why not (`platform.codesign.resign_adhoc`).
Resign = Callable[[Path], Awaitable[str | None]]


class StampRefused(Exception):
    """An app that could not be stamped, said as the rest of a "not stamped: ..." line."""


@dataclass(frozen=True)
class Stamp:
    """What an app says it was built from."""

    commit: str
    dirty: bool = False

    @property
    def short(self) -> str:
        return self.commit[:SHORT]


def stamp_of(info: Mapping[str, Any]) -> Stamp | None:
    """The stamp an app's Info.plist holds, or None when it holds none SimMirror wrote."""
    commit = info.get(SHA_KEY)
    if not isinstance(commit, str) or not _COMMIT.match(commit):
        return None
    return Stamp(commit, info.get(DIRTY_KEY) is True)


def _clonefile(source: Path, target: Path) -> bool:
    """A copy-on-write clone of a folder and all it holds, in one call: APFS's ``clonefile``. Whether it was made."""
    name = ctypes.util.find_library("c")
    if name is None:
        return False
    clone = getattr(ctypes.CDLL(name, use_errno=True), "clonefile", None)
    if clone is None:
        return False
    return bool(clone(os.fsencode(source), os.fsencode(target), 0) == 0)


def copy_app(source: Path, target: Path, clonefile: Callable[[Path, Path], bool] = _clonefile) -> None:
    """Copy an app bundle: cloned where the disk can, else copied file by file, its links kept as links."""
    if not clonefile(source, target):
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(source, target, symlinks=True)


def discard(copy: Path) -> None:
    """Remove a stamped copy once it was installed, or could not be."""
    shutil.rmtree(copy, ignore_errors=True)


async def stamp_app(
    app: Path, stamp: Stamp, resign: Resign, *, clonefile: Callable[[Path, Path], bool] = _clonefile
) -> Path:
    """A copy of a built simulator app with `stamp` in its Info.plist, signed again if the app was signed -- made in
    the `STAMPED` folder beside the app's own build folder. Raises `StampRefused`, leaving no copy."""
    folder = app.parent.parent / STAMPED
    copy = folder / app.name
    discard(copy)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        copy_app(app, copy, clonefile)
        plist = copy / "Info.plist"
        raw = plist.read_bytes()
        info = plistlib.loads(raw)
        if not isinstance(info, dict):
            raise StampRefused(f"{app.name}'s Info.plist is not a dictionary")
        info[SHA_KEY], info[DIRTY_KEY] = stamp.commit, stamp.dirty
        binary = raw.startswith(b"bplist")
        # Written as a new file: a clone's blocks are its original's until written, and a link is never followed.
        plist.unlink()
        plist.write_bytes(plistlib.dumps(info, fmt=plistlib.FMT_BINARY if binary else plistlib.FMT_XML))
        if (copy / "_CodeSignature").is_dir():
            refused = await resign(copy)
            if refused is not None:
                raise StampRefused(f"it could not be signed again: {refused}")
    except StampRefused:
        discard(copy)
        raise
    except (OSError, plistlib.InvalidFileException, ValueError, shutil.Error) as exc:
        discard(copy)
        raise StampRefused(f"{app.name} could not be copied and stamped: {exc}") from exc
    return copy


def described(stamp: Stamp) -> str:
    """How an answer names a stamp."""
    return f"{stamp.short}" + (" with changes not committed" if stamp.dirty else "")


def compared(mark: Stamp, now: Source, folder: Path) -> str:
    """Whether an app stamped `mark` is the code `folder` holds now, said so an agent knows whether to build again."""
    if now.commit is None:
        return f"{folder} names no commit to compare it with ({now.line})"
    if now.commit != mark.commit:
        return (
            f"not the code in {folder}, which is at {now.commit[:SHORT]} now: build again with sim_build_run "
            "before you show or record it"
        )
    if mark.dirty or now.dirty:
        when = "when it was built" if mark.dirty else "in the folder now"
        return (
            f"built from {mark.short}, the commit {folder} is on, but there were changes not committed {when}: "
            "build again to be sure it holds them"
        )
    return f"the code in {folder}: {mark.short}, with nothing uncommitted then or now"
