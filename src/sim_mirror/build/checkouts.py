# SPDX-License-Identifier: Apache-2.0
"""Which folder a build runs in: the scope's build folder, or the same folder of another checkout of its repository.

An agent working in a git worktree -- a second checkout of the project's repository, on another branch -- wants that
worktree built, not the folder SimMirror was started in. So a build may run in

* the build folder (`Policy.build_folder`), or a folder inside it, as always; or
* the same place in another checkout of the repository the build folder is in: its main checkout, or one of its linked
  worktrees. When the build folder is ``repo/ios``, the worktree ``wt`` builds in ``wt/ios``.

A checkout is known from its files, with no git run: a main checkout holds a ``.git`` folder, and a linked worktree a
``.git`` file naming its own folder in the repository (``<repo>/.git/worktrees/<name>``), which must name the worktree
back. A folder that only claims to be a checkout -- a ``.git`` linked or copied from another -- is not one, so nothing
outside the repository's own checkouts is built.

A main checkout made with ``git init --separate-git-dir`` (or ``git clone --separate-git-dir``) holds a ``.git`` file
too, naming the repository's git folder somewhere else -- and git leaves nothing in that folder naming the checkout
back (``git worktree list`` gives the git folder itself as the main worktree), so a copy of the file would be taken for
it just as well. Such a checkout is known

* as the build folder's own (`home_checkout`), which the host chose; or
* when its git folder names it back as git's own ``core.worktree``: in the git folder's ``config``, where
  ``git config core.worktree "$PWD"`` run in the main checkout writes it, and which git itself then goes by. That is a
  link both ways, as a worktree's is, kept where a worktree's is; git in a linked worktree leaves it out of account.

Without either, a session whose build folder is in one of its worktrees cannot reach the main checkout: nothing on
disk tells it from a copy. Its linked worktrees are known as any are, from the repository naming them back.
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

GIT = ".git"
#: The longest folder a build call may name.
FOLDER_MAX = 1024
#: A ``.git`` file and the files it points at hold one path each.
POINTER_MAX = 4096
#: Larger than any ``config`` git keeps in a git folder; a bigger one is not read.
CONFIG_MAX = 65536
#: A section header of git's config -- ``[core]``, ``[remote "origin"]`` -- its name, its subsection, and what may
#: follow it on its line.
_SECTION = re.compile(r'\[\s*([A-Za-z0-9.-]+)\s*("(?:[^"\\]|\\.)*")?\s*\](.*)\Z')
#: A variable of git's config: its name and, after ``=``, its value as written.
_VARIABLE = re.compile(r"([A-Za-z][A-Za-z0-9-]*)\s*(?:=(.*))?\Z")
#: The escapes git reads in a config value.
_ESCAPES = {'"': '"', "\\": "\\", "n": "\n", "t": "\t", "b": "\b"}


@dataclass(frozen=True)
class Checkout:
    """A working tree of a repository: its top folder, and the repository's git folder every checkout shares."""

    top: Path
    repository: Path


def _kind(path: Path) -> int | None:
    """The file type of what is at `path` -- following a link -- or None when there is nothing to read there."""
    try:
        return stat.S_IFMT(os.stat(path).st_mode)
    except (OSError, ValueError):
        return None


def _read(path: Path) -> str | None:
    """The one line a git pointer file holds, or None."""
    try:
        with path.open("rb") as file:
            raw = file.read(POINTER_MAX + 1)
    except OSError:
        return None
    if len(raw) > POINTER_MAX:
        return None
    try:
        return raw.decode("utf-8").strip() or None
    except UnicodeDecodeError:
        return None


def _linked(top: Path, pointer: Path) -> Path | None:
    """The repository's git folder when `top` is one of its linked worktrees that it names back, else None."""
    said = _read(pointer)
    if said is None or not said.startswith("gitdir:"):
        return None
    own = (top / said.removeprefix("gitdir:").strip()).resolve()
    back, common = _read(own / "gitdir"), _read(own / "commondir")
    if back is None or common is None or (own / back).resolve() != top / GIT:
        return None
    repository = (own / common).resolve()
    return repository if own.parent == repository / "worktrees" else None


def _separate(pointer: Path) -> Path | None:
    """The git folder a main checkout's ``.git`` file names when the repository keeps it elsewhere -- a repository's own
    git folder, not a linked worktree's -- else None."""
    said = _read(pointer)
    if said is None or not said.startswith("gitdir:"):
        return None
    repository = (pointer.parent / said.removeprefix("gitdir:").strip()).resolve()
    if _kind(repository) != stat.S_IFDIR or _kind(repository / "HEAD") != stat.S_IFREG:
        return None
    return repository if _kind(repository / "commondir") is None else None


def _value(written: str) -> str | None:
    """A config value as git reads it: its quotes taken off, its escapes read, cut at a comment and trimmed -- or None
    for one git would not read, or one that goes on to the next line."""
    out: list[str] = []
    kept, quoted, escaped = 0, False, False
    for char in written.lstrip():
        if escaped:
            if char not in _ESCAPES:
                return None
            out.append(_ESCAPES[char])
            kept, escaped = len(out), False
        elif char == "\\":
            escaped = True
        elif char == '"':
            quoted, kept = not quoted, len(out)
        elif char in "#;" and not quoted:
            break
        else:
            out.append(char)
            if quoted or not char.isspace():
                kept = len(out)
    return None if escaped or quoted else "".join(out[:kept])


def _config(path: Path) -> str | None:
    try:
        with path.open("rb") as file:
            raw = file.read(CONFIG_MAX + 1)
        return raw.decode("utf-8") if len(raw) <= CONFIG_MAX else None
    except (OSError, UnicodeDecodeError):
        return None


def _core(repository: Path) -> dict[str, str] | None:
    """The ``[core]`` variables a git folder's ``config`` sets, by lowercase name, the last one written winning -- or
    None when it cannot be read as git writes it. A file it includes is not read."""
    said = _config(repository / "config")
    if said is None:
        return None
    values: dict[str, str] = {}
    section: str | None = None
    for line in said.splitlines():
        text = line.strip()
        header = _SECTION.match(text) if text.startswith("[") else None
        if header is not None:
            section = None if header.group(2) else header.group(1).lower()
            text = header.group(3).strip()
        if not text or text[0] in "#;":
            continue
        variable = _VARIABLE.match(text)
        if variable is None:
            return None
        value = _value(variable.group(2)) if variable.group(2) is not None else "true"
        if value is None:
            return None
        if section == "core":
            values[variable.group(1).lower()] = value
    return values


def _named_back(repository: Path) -> Path | None:
    """The main checkout a repository's git folder names as git's ``core.worktree`` -- relative to the git folder, as
    git reads it -- or None when it names none."""
    core = _core(repository)
    worktree = core.get("worktree") if core is not None else None
    return (repository / worktree).resolve() if worktree else None


def is_bare(repository: Path) -> bool:
    """Whether a repository's git folder says it is bare: a repository with no main checkout at all."""
    core = _core(repository)
    return core is not None and core.get("bare", "").lower() in ("true", "yes", "on", "1")


def checkout_of(folder: Path, *, known: Checkout | None = None) -> Checkout | None:
    """The checkout a folder is in, from the nearest ``.git`` at or above it -- None outside a repository, or when that
    ``.git`` is not one a checkout can be known by. `known` is a main checkout known otherwise (`home_checkout`), taken
    for what its ``.git`` file says. A main checkout whose git folder is elsewhere is known too when that folder names
    it back (``core.worktree``)."""
    real = folder.resolve()
    for top in (real, *real.parents):
        pointer = top / GIT
        kind = _kind(pointer)
        if kind == stat.S_IFDIR:
            return Checkout(top, top / GIT) if pointer.resolve() == top / GIT else None
        if kind == stat.S_IFREG:
            repository = _linked(top, pointer)
            if repository is not None:
                return Checkout(top, repository)
            if known is not None and known.top == top:
                return known
            repository = _separate(pointer)
            if repository is not None and _named_back(repository) == top:
                return Checkout(top, repository)
            return None
    return None


def home_checkout(home: Path) -> Checkout | None:
    """The checkout the build folder is in: as `checkout_of` knows one, or else a main checkout whose ``.git`` file
    names its repository's git folder elsewhere -- trusted here, and only here, because the host chose the build
    folder."""
    found = checkout_of(home)
    if found is not None:
        return found
    real = home.resolve()
    for top in (real, *real.parents):
        kind = _kind(top / GIT)
        if kind == stat.S_IFREG:
            repository = _separate(top / GIT)
            return Checkout(top, repository) if repository is not None else None
        if kind is not None:
            return None
    return None


def main_of(checkout: Checkout) -> Checkout | None:
    """The main checkout of a checkout's repository -- the folder its ``.git`` folder is in; for a git folder kept
    elsewhere, the checkout itself when its ``.git`` file names that folder, or else the one the folder names back
    (``core.worktree``) -- or None when there is none to know, as for a bare repository."""
    if _kind(checkout.top / GIT) == stat.S_IFREG and _separate(checkout.top / GIT) == checkout.repository:
        return checkout
    for top in (checkout.repository.parent, _named_back(checkout.repository)):
        main = checkout_of(top) if top is not None else None
        if main is not None and main.top == top and main.repository == checkout.repository:
            return main
    return None


def build_folder_for(asked: Path, home: Path, *, exact: bool) -> Path | None:
    """Where a build asked for at `asked` runs, for a scope whose build folder is `home` -- or None when that is not a
    folder this scope may build.

    `exact` is for a folder an agent named: kept when it is inside the build folder, or inside the same folder of
    another checkout, and taken to that folder when it holds it. Otherwise `asked` is where the agent's client works:
    anywhere in a checkout of the repository builds that checkout's build folder.
    """
    home, asked = home.resolve(), asked.resolve()
    own = home_checkout(home)
    there = checkout_of(asked, known=own)
    if own is not None and there is not None and there.repository == own.repository:
        place = there.top / home.relative_to(own.top)
        if exact and not asked.is_relative_to(place):
            return place if place.is_relative_to(asked) else None
        return asked if exact else place
    if asked.is_relative_to(home):
        return asked if exact else home
    if exact:
        return None
    return home if home.is_relative_to(asked) else None
