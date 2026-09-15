# SPDX-License-Identifier: Apache-2.0
"""Private folders, files written whole, a lock across processes, and secrets that are replaced when they cannot be
trusted."""

from __future__ import annotations

import os
import stat
import threading
import time
from pathlib import Path

import pytest

from sim_mirror.storage import private


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_a_private_folder_is_the_owners_only_even_when_it_existed(tmp_path: Path) -> None:
    open_folder = tmp_path / "open"
    open_folder.mkdir(mode=0o755)
    assert private.ensure_private_dir(open_folder) == open_folder and mode(open_folder) == 0o700
    assert mode(private.ensure_private_dir(tmp_path / "x" / "y")) == 0o700


def test_a_private_file_is_written_whole_and_the_owners_only(tmp_path: Path) -> None:
    path = tmp_path / "folder" / "token"
    private.write_private(path, b"secret")
    assert path.read_bytes() == b"secret" and mode(path) == 0o600
    private.write_private(path, b"again")
    assert path.read_bytes() == b"again" and sorted(p.name for p in path.parent.iterdir()) == ["token"]


def test_a_write_that_fails_leaves_the_old_file_and_no_temporary_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "token"
    private.write_private(path, b"old")

    def refuse(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(private.os, "replace", refuse)
    with pytest.raises(OSError, match="disk full"):
        private.write_private(path, b"new")
    assert path.read_bytes() == b"old" and [p.name for p in tmp_path.iterdir()] == ["token"]


def test_the_lock_keeps_a_second_holder_waiting(tmp_path: Path) -> None:
    order: list[str] = []
    lock = tmp_path / "locks" / ".lock"

    def second() -> None:
        with private.file_lock(lock):
            order.append("second")

    with private.file_lock(lock):
        thread = threading.Thread(target=second)
        thread.start()
        time.sleep(0.05)
        order.append("first")
    thread.join(timeout=2)
    assert order == ["first", "second"] and mode(lock) == 0o600


def test_a_secret_is_made_once_and_read_back(tmp_path: Path) -> None:
    path = tmp_path / "state" / "token"
    made = private.read_or_create_secret(path)
    assert len(made) == 32 and mode(path) == 0o600
    assert private.read_or_create_secret(path) == made
    replaced = private.replace_secret(path, size=16)
    assert len(replaced) == 16 and private.read_or_create_secret(path, size=16) == replaced


@pytest.mark.parametrize("content", ["not hex\n", "abcd\n"])
def test_a_secret_that_is_not_one_is_replaced(tmp_path: Path, content: str) -> None:
    path = tmp_path / "token"
    path.write_text(content)
    assert private.read_or_create_secret(path).hex() + "\n" == path.read_text()


def test_a_secret_others_could_read_is_replaced_unless_the_folder_cannot_keep_anything_private(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "token"
    first = private.read_or_create_secret(path)
    os.chmod(path, 0o644)
    second = private.read_or_create_secret(path)
    assert second != first and mode(path) == 0o600 and "replaced" in caplog.text
    os.chmod(path, 0o644)
    monkeypatch.setattr(private, "holds_private_files", lambda folder: False)
    assert private.read_or_create_secret(path) == second and "cannot keep a file private" in caplog.text


def test_a_normal_folder_holds_private_files(tmp_path: Path) -> None:
    assert private.holds_private_files(tmp_path) is True and list(tmp_path.iterdir()) == []
