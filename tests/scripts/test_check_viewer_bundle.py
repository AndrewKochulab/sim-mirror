# SPDX-License-Identifier: Apache-2.0
"""The viewer bundle check: the committed page is what the source builds, and every script stays within its budget."""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

import pytest

import check_viewer_bundle
from check_viewer_bundle import BUDGET, BUDGETED, BUNDLE_DIR, git, gzipped_size, main, problems


def built(root: Path, sizes: dict[str, bytes]) -> Path:
    for rel, data in sizes.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


def clean(args: Sequence[str]) -> str:
    return ""


SMALL = {rel: b"export const x = 1\n" * 50 for rel in BUDGETED}


def test_a_committed_bundle_that_matches_its_build_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = built(tmp_path, SMALL)
    asked: list[Sequence[str]] = []

    def status(args: Sequence[str]) -> str:
        asked.append(args)
        return ""

    assert problems(root, status) == []
    assert asked == [["status", "--porcelain", "--untracked-files=all", "--", BUNDLE_DIR]]
    assert main(root, clean) == 0
    assert "viewer bundle ok" in capsys.readouterr().out


def test_a_bundle_that_drifted_or_was_not_built_is_named(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = built(tmp_path, {BUDGETED[1]: b"page"})

    def drifted(args: Sequence[str]) -> str:
        return f" M {BUNDLE_DIR}/start.js\n?? {BUNDLE_DIR}/new.js\n\n"

    assert problems(root, drifted) == [
        f"{BUNDLE_DIR}/start.js: not what viewer/ builds -- run `make viewer-bundle` and commit the result",
        f"{BUNDLE_DIR}/new.js: not what viewer/ builds -- run `make viewer-bundle` and commit the result",
        "viewer/dist/index.js: not built -- run `make viewer-bundle` and commit the result",
    ]
    assert main(root, drifted) == 1
    assert "the viewer bundle needs attention" in capsys.readouterr().err


def test_a_bundle_over_its_budget_is_named(tmp_path: Path) -> None:
    heavy = os.urandom(BUDGET + 5_000)
    root = built(tmp_path, {**SMALL, BUDGETED[0]: heavy})
    [problem] = problems(root, clean)
    assert problem.startswith("viewer/dist/index.js: ") and problem.endswith(" KB gzipped, over the 45 KB budget")
    assert gzipped_size(root / BUDGETED[0]) > BUDGET


def test_git_runs_in_the_repository() -> None:
    assert git(["rev-parse", "--is-inside-work-tree"]).strip() == "true"


def test_this_repository_s_bundle_is_current_when_built(capsys: pytest.CaptureFixture[str]) -> None:
    if not (check_viewer_bundle.REPO_ROOT / BUDGETED[0]).is_file():
        pytest.skip("viewer/dist is not built here; CI's viewer job builds it before this check")
    assert main() == 0
