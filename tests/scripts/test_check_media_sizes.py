# SPDX-License-Identifier: Apache-2.0
"""The media check: pictures in docs/media stay within their limits, and videos go to a release instead."""

from __future__ import annotations

from pathlib import Path

import pytest

import check_media_sizes
from check_media_sizes import KB, MB, main, problems


def sized(root: Path, files: dict[str, int]) -> list[str]:
    for rel, size in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\0" * size)
    return sorted(files)


def test_pictures_within_their_limits_pass(tmp_path: Path) -> None:
    files = {
        "docs/media/hero.gif": 3 * MB,
        "docs/media/viewer.png": 400 * KB,
        "docs/media/README.md": 5 * MB,
        "elsewhere/big.mov": 50 * MB,
    }
    assert problems(tmp_path, sized(tmp_path, files)) == []


def test_each_picture_over_its_limit_and_each_video_is_named(tmp_path: Path) -> None:
    files = {"docs/media/hero.gif": 3 * MB + 1, "docs/media/shot.PNG": 401 * KB, "docs/media/demo.mp4": 10}
    assert problems(tmp_path, sized(tmp_path, files)) == [
        "docs/media/demo.mp4: not a picture this folder takes (.gif, .jpeg, .jpg, .png, .svg, .webp); attach videos to "
        "a release",
        "docs/media/hero.gif: 3.0 MB, over the 3.0 MB a .gif may be",
        "docs/media/shot.PNG: 401 KB, over the 400 KB a .png may be",
    ]


def test_the_folder_as_a_whole_has_a_limit(tmp_path: Path) -> None:
    files = {f"docs/media/{n}.gif": 3 * MB for n in range(9)}
    assert problems(tmp_path, sized(tmp_path, files)) == ["docs/media/: 27.0 MB in all, over 25.0 MB"]


def test_this_repository_passes(capsys: pytest.CaptureFixture[str]) -> None:
    assert main() == 0
    assert "media ok" in capsys.readouterr().out


def test_a_heavy_repository_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    files = sized(tmp_path, {"docs/media/shot.png": 500 * KB})
    monkeypatch.setattr(check_media_sizes, "repo_files", lambda root: files)
    assert main(tmp_path) == 1
    assert "docs/media/shot.png: 500 KB" in capsys.readouterr().err
