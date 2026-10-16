# SPDX-License-Identifier: Apache-2.0
"""Git checkouts laid out as git leaves them -- a main checkout and its linked worktrees -- with no git run.

`build.checkouts` knows a checkout from these files alone, so a test lays them out instead: ``.git`` as a folder for a
main checkout; for a worktree, a ``.git`` file naming ``<repo>/.git/worktrees/<name>``, which holds ``gitdir`` (the
worktree's ``.git``, absolute) and ``commondir`` (``../..``), as ``git worktree add`` writes them.
"""

from __future__ import annotations

from pathlib import Path


def main_checkout(folder: Path) -> Path:
    """A repository's main checkout at `folder`: answers the folder."""
    (folder / ".git" / "worktrees").mkdir(parents=True, exist_ok=True)
    (folder / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    return folder


def linked_worktree(repository: Path, folder: Path, name: str | None = None) -> Path:
    """A linked worktree of the main checkout at `repository`, at `folder`: answers the folder."""
    own = repository / ".git" / "worktrees" / (name or folder.name)
    own.mkdir(parents=True)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ".git").write_text(f"gitdir: {own}\n", encoding="utf-8")
    (own / "gitdir").write_text(f"{folder / '.git'}\n", encoding="utf-8")
    (own / "commondir").write_text("../..\n", encoding="utf-8")
    (own / "HEAD").write_text(f"ref: refs/heads/{name or folder.name}\n", encoding="utf-8")
    return folder
