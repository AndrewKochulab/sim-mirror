# SPDX-License-Identifier: Apache-2.0
"""The file list the checks share: tracked and untracked files, never ignored ones or folders."""

from __future__ import annotations

import subprocess
from pathlib import Path

import _repo


def test_the_repository_lists_its_own_files() -> None:
    files = _repo.repo_files()
    assert "pyproject.toml" in files and "scripts/_repo.py" in files
    assert not any(name.startswith(".git/") for name in files)


def test_untracked_files_count_and_ignored_ones_do_not(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("ignored.txt\n")
    (tmp_path / "ignored.txt").write_text("x")
    (tmp_path / "kept.py").write_text("x")
    (tmp_path / "folder").mkdir()
    (tmp_path / "folder" / "inside.md").write_text("x")
    assert _repo.repo_files(tmp_path) == [".gitignore", "folder/inside.md", "kept.py"]


def test_text_is_read_and_binary_is_not(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "b.bin").write_bytes(b"\xff\xfe\x00")
    assert _repo.read_text(tmp_path / "a.txt") == "hello"
    assert _repo.read_text(tmp_path / "b.bin") is None
