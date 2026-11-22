# SPDX-License-Identifier: Apache-2.0
"""Where a build keeps what it compiles: the scope's DerivedData folder, or one for each checkout.

Under ``build.derived_data = "scope"``, the default, every build of a scope uses the one folder its `StateStore` names
(`StateStore.derived_data`), whichever checkout of the project it builds: a git worktree's build reuses what the last
build compiled, and only one runs at a time, since two xcodebuilds in one DerivedData folder would undo each other.

Under ``"checkout"``, each checkout -- the main one, and every git worktree -- builds into a folder of its own, beside
the scope's and named after it: the scope's folder's name, the checkout's folder name, and a short hash of its real
path, so two worktrees with the same name are two folders (`checkout_folder`). Builds of different checkouts then run
side by side (`runner.Runner`). The store is asked nothing new: the folder sits where the scope's does, inside the
folders an app may be installed from.

Each such folder holds a marker naming its checkout (`MARKER`). A worktree that is deleted leaves gigabytes behind, so
each build in this mode deletes the scope's folders whose checkout is gone (`prune`) -- only folders named as this
module names them, holding a marker whose checkout matches the name, and never one a build is using.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from collections.abc import Collection
from pathlib import Path

from sim_mirror.build.checkouts import GIT, checkout_of, home_checkout

#: The file in a checkout's DerivedData folder naming the checkout it is for: its real path, on one line.
MARKER = "sim-mirror-checkout.txt"
#: The longest a marker is read.
MARKER_MAX = 4096
#: How many hex digits of the checkout's path hash a folder's name ends with.
HASH_DIGITS = 8
_SLUG = re.compile(r"[^A-Za-z0-9_.-]+")


def _slug(name: str) -> str:
    return _SLUG.sub("-", name).strip("-._")[:48] or "checkout"


def _digest(top: Path) -> str:
    return hashlib.blake2b(str(top).encode("utf-8"), digest_size=HASH_DIGITS // 2).hexdigest()


def checkout_top(folder: Path, home: Path) -> Path:
    """The real path of the checkout a build of `folder` builds -- the top of its git checkout, a worktree's own --
    for a scope whose build folder is `home`; the build folder itself where it is in no checkout."""
    found = checkout_of(folder, known=home_checkout(home))
    return found.top if found is not None else home.resolve()


def checkout_folder(scope_folder: Path, top: Path) -> Path:
    """The DerivedData folder of the checkout at `top`, for a scope whose own is `scope_folder`: beside it, named
    ``<its name>-<checkout name>-<hash of the checkout's real path>``."""
    return scope_folder.with_name(f"{scope_folder.name}-{_slug(top.name)}-{_digest(top)}")


def mark(derived: Path, top: Path) -> None:
    """Make a checkout's DerivedData folder, naming its checkout in it, so it can be deleted once the checkout is."""
    derived.mkdir(parents=True, exist_ok=True)
    marker = derived / MARKER
    if _marked(marker) != top:
        marker.write_text(f"{top}\n", encoding="utf-8")


def _marked(marker: Path) -> Path | None:
    """The checkout a marker names, or None for one that cannot be read."""
    try:
        with marker.open("rb") as file:
            raw = file.read(MARKER_MAX + 1)
        said = raw.decode("utf-8").strip() if len(raw) <= MARKER_MAX else ""
    except (OSError, UnicodeDecodeError):
        return None
    return Path(said) if said.startswith("/") and "\n" not in said and "\x00" not in said else None


def _gone(top: Path) -> bool:
    """Whether a checkout is no longer there: its folder or its ``.git`` deleted, as removing a worktree does. One that
    cannot be looked at -- not allowed to -- is taken to be there."""
    try:
        os.stat(top / GIT)
    except (FileNotFoundError, NotADirectoryError):
        return True
    except OSError:
        return False
    return False


def prune(scope_folder: Path, keep: Collection[Path] = ()) -> list[Path]:
    """Delete the checkout DerivedData folders of a scope whose checkout is gone -- but those in `keep`, which builds
    are using. Answers the folders deleted. Only a folder named as `checkout_folder` names one, for the checkout its
    marker names, is deleted: nothing else beside the scope's folder is touched."""
    kept = {str(folder) for folder in keep}
    prefix = f"{scope_folder.name}-"
    try:
        candidates = sorted(entry for entry in scope_folder.parent.iterdir() if entry.name.startswith(prefix))
    except OSError:
        return []
    deleted: list[Path] = []
    for folder in candidates:
        if str(folder) in kept or folder.is_symlink() or not folder.is_dir():
            continue
        top = _marked(folder / MARKER)
        if top is None or checkout_folder(scope_folder, top) != folder or not _gone(top):
            continue
        shutil.rmtree(folder, ignore_errors=True)
        if not folder.exists():
            deleted.append(folder)
    return deleted
