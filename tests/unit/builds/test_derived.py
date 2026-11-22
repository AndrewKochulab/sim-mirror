# SPDX-License-Identifier: Apache-2.0
"""A checkout's DerivedData folder: named after the checkout beside the scope's, marked, and deleted once it is gone."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from sim_mirror.build import derived
from sim_mirror.build.derived import MARKER, checkout_folder, checkout_top, mark, prune
from sim_mirror.testing.checkouts import linked_worktree, main_checkout

SCOPE_FOLDER_NAME = "SimMirror-project-demo-1a2b3c4d"


def test_a_checkouts_folder_sits_beside_the_scopes_named_after_it_and_a_hash_of_its_real_path(tmp_path: Path) -> None:
    scope_folder = tmp_path / "DerivedData" / SCOPE_FOLDER_NAME
    one = checkout_folder(scope_folder, Path("/work/demo-worktrees/feature-x"))
    other = checkout_folder(scope_folder, Path("/work/elsewhere/feature-x"))
    assert one.parent == scope_folder.parent and one.name.startswith(f"{SCOPE_FOLDER_NAME}-feature-x-")
    assert len(one.name.rsplit("-", 1)[1]) == 8 and one != other, "two worktrees of one name are two folders"
    assert checkout_folder(scope_folder, Path("/work/demo-worktrees/feature-x")) == one
    # A name that would not make a tidy folder name is made into one; a name of nothing at all is still named.
    assert checkout_folder(scope_folder, Path("/work/my feature (2)")).name.startswith(
        f"{SCOPE_FOLDER_NAME}-my-feature-2-"
    )
    assert checkout_folder(scope_folder, Path("/work/...")).name.startswith(f"{SCOPE_FOLDER_NAME}-checkout-")


def test_the_checkout_of_a_build_is_the_top_of_its_worktree_or_the_build_folder_outside_git(tmp_path: Path) -> None:
    home = main_checkout(tmp_path / "demo")
    (home / "ios").mkdir()
    worktree = linked_worktree(home, tmp_path / "demo-feature-x")
    (worktree / "ios").mkdir()
    assert checkout_top(worktree / "ios", home / "ios") == worktree.resolve()
    assert checkout_top(home / "ios", home / "ios") == home.resolve()
    plain = tmp_path / "plain"
    (plain / "App").mkdir(parents=True)
    assert checkout_top(plain / "App", plain) == plain.resolve()


def test_a_folder_is_made_and_marked_with_its_checkout_once(tmp_path: Path) -> None:
    folder = tmp_path / "DerivedData" / f"{SCOPE_FOLDER_NAME}-feature-x-0011aabb"
    mark(folder, Path("/work/demo-worktrees/feature-x"))
    marker = folder / MARKER
    assert marker.read_text(encoding="utf-8") == "/work/demo-worktrees/feature-x\n"
    written = marker.stat().st_mtime_ns
    os.utime(marker, ns=(1, 1))
    mark(folder, Path("/work/demo-worktrees/feature-x"))
    assert marker.stat().st_mtime_ns == 1 != written, "a marker that already says so is left as it is"
    mark(folder, Path("/work/demo-worktrees/feature-y"))
    assert marker.read_text(encoding="utf-8") == "/work/demo-worktrees/feature-y\n"


def made(scope_folder: Path, top: Path) -> Path:
    folder = checkout_folder(scope_folder, top)
    mark(folder, top)
    (folder / "Build").mkdir()
    return folder


def test_the_folders_of_checkouts_that_are_gone_are_deleted_and_nothing_else_is(tmp_path: Path) -> None:
    scope_folder = tmp_path / "DerivedData" / SCOPE_FOLDER_NAME
    scope_folder.mkdir(parents=True)
    home = main_checkout(tmp_path / "demo")
    here = linked_worktree(home, tmp_path / "demo-feature-x")
    gone = linked_worktree(home, tmp_path / "demo-feature-y")
    kept, deleted, busy = made(scope_folder, here), made(scope_folder, gone), made(scope_folder, tmp_path / "gone-z")
    removed = linked_worktree(home, tmp_path / "demo-feature-w")
    left = made(scope_folder, removed)
    (removed / ".git").unlink()
    # Beside them: folders this did not make, or whose marker does not match their name, are never touched.
    unmarked = scope_folder.with_name(f"{SCOPE_FOLDER_NAME}-unmarked-00000000")
    unmarked.mkdir()
    renamed = scope_folder.with_name(f"{SCOPE_FOLDER_NAME}-renamed-00000000")
    renamed.mkdir()
    (renamed / MARKER).write_text(f"{tmp_path / 'gone-q'}\n", encoding="utf-8")
    unreadable = scope_folder.with_name(f"{SCOPE_FOLDER_NAME}-unreadable-00000000")
    unreadable.mkdir()
    (unreadable / MARKER).write_bytes(b"\xff\xfe")
    relative = scope_folder.with_name(f"{SCOPE_FOLDER_NAME}-relative-00000000")
    relative.mkdir()
    (relative / MARKER).write_text("demo-feature-y\n", encoding="utf-8")
    huge = scope_folder.with_name(f"{SCOPE_FOLDER_NAME}-huge-00000000")
    huge.mkdir()
    (huge / MARKER).write_text("/" + "x" * derived.MARKER_MAX, encoding="utf-8")
    link = scope_folder.with_name(f"{SCOPE_FOLDER_NAME}-link")
    link.symlink_to(deleted)
    stray = scope_folder.with_name(f"{SCOPE_FOLDER_NAME}-file")
    stray.write_text("not a folder", encoding="utf-8")
    other_scope = made(tmp_path / "DerivedData" / "SimMirror-project-other-9f8e7d6c", tmp_path / "gone-o")
    shutil.rmtree(gone)
    assert prune(scope_folder, keep={busy}) == [left, deleted]
    assert kept.is_dir() and busy.is_dir() and not deleted.exists() and not left.exists()
    for untouched in (scope_folder, unmarked, renamed, unreadable, relative, huge, stray, other_scope):
        assert untouched.exists()
    assert link.is_symlink()
    assert prune(scope_folder) == [busy], "once no build uses it"


def test_pruning_a_scope_with_no_folders_yet_deletes_nothing(tmp_path: Path) -> None:
    assert prune(tmp_path / "no-such-place" / SCOPE_FOLDER_NAME) == []


def test_a_checkout_that_cannot_be_looked_at_is_taken_to_be_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope_folder = tmp_path / "DerivedData" / SCOPE_FOLDER_NAME
    folder = made(scope_folder, tmp_path / "locked")
    real_stat = os.stat

    def stat(path: object, *args: object, **kwargs: object) -> os.stat_result:
        if str(path).startswith(str(tmp_path / "locked")):
            raise PermissionError(13, "Permission denied")
        return real_stat(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(derived.os, "stat", stat)
    assert prune(scope_folder) == [] and folder.is_dir()


def test_a_folder_that_could_not_be_deleted_is_not_said_to_be(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scope_folder = tmp_path / "DerivedData" / SCOPE_FOLDER_NAME
    folder = made(scope_folder, tmp_path / "gone")
    monkeypatch.setattr(derived.shutil, "rmtree", lambda path, ignore_errors=False: None)
    assert prune(scope_folder) == [] and folder.is_dir()
