# SPDX-License-Identifier: Apache-2.0
"""Git checkouts laid out as git leaves them -- a main checkout and its linked worktrees -- with no git run.

`build.checkouts` knows a checkout from these files alone, so a test lays them out instead: ``.git`` as a folder for a
main checkout; for a worktree, a ``.git`` file naming ``<repo>/.git/worktrees/<name>``, which holds ``gitdir`` (the
worktree's ``.git``, absolute) and ``commondir`` (``../..``), as ``git worktree add`` writes them. A main checkout made
with ``--separate-git-dir`` holds a ``.git`` file naming its git folder instead, as ``git init`` writes it, and that
folder names it back only when ``core.worktree`` says so.
"""

from __future__ import annotations

from pathlib import Path


def main_checkout(folder: Path) -> Path:
    """A repository's main checkout at `folder`: answers the folder."""
    (folder / ".git" / "worktrees").mkdir(parents=True, exist_ok=True)
    (folder / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    return folder


#: The ``config`` ``git init`` writes in a new repository's git folder (git 2.50, macOS).
INIT_CONFIG = (
    "[core]\n"
    "\trepositoryformatversion = 0\n"
    "\tfilemode = true\n"
    "\tbare = false\n"
    "\tlogallrefupdates = true\n"
    "\tignorecase = true\n"
    "\tprecomposeunicode = true\n"
)


def config_value(text: str) -> str:
    """A value as ``git config`` writes it: quoted whole when it holds what git would read otherwise."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    special = escaped != text or any(char in text for char in "#;") or text != text.strip()
    return f'"{escaped}"' if special else text


def separate_main_checkout(folder: Path, git_dir: Path, *, named_back: bool = False) -> Path:
    """A repository's main checkout at `folder` whose git folder is `git_dir`, as ``git init --separate-git-dir``
    leaves it -- and, `named_back`, with its git folder naming it as ``git config core.worktree "$PWD"`` run in it
    does: answers the folder."""
    (git_dir / "worktrees").mkdir(parents=True, exist_ok=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    worktree = f"\tworktree = {config_value(str(folder))}\n" if named_back else ""
    (git_dir / "config").write_text(INIT_CONFIG + worktree, encoding="utf-8")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ".git").write_text(f"gitdir: {git_dir}\n", encoding="utf-8")
    return folder


def _git_dir(repository: Path) -> Path:
    """A main checkout's git folder: its ``.git``, or the folder its ``.git`` file names."""
    pointer = repository / ".git"
    if pointer.is_file():
        return Path(pointer.read_text(encoding="utf-8").removeprefix("gitdir:").strip())
    return pointer


def linked_worktree(repository: Path, folder: Path, name: str | None = None) -> Path:
    """A linked worktree of the main checkout at `repository`, at `folder`: answers the folder."""
    own = _git_dir(repository) / "worktrees" / (name or folder.name)
    own.mkdir(parents=True)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ".git").write_text(f"gitdir: {own}\n", encoding="utf-8")
    (own / "gitdir").write_text(f"{folder / '.git'}\n", encoding="utf-8")
    (own / "commondir").write_text("../..\n", encoding="utf-8")
    (own / "HEAD").write_text(f"ref: refs/heads/{name or folder.name}\n", encoding="utf-8")
    return folder
