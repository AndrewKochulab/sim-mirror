# SPDX-License-Identifier: Apache-2.0
"""The host-neutral check: the repository is clean, and a host's words are caught wherever they are."""

from __future__ import annotations

from pathlib import Path

import pytest

import check_host_neutral as check


def test_the_repository_is_clean(capsys: pytest.CaptureFixture[str]) -> None:
    assert check.main() == 0
    assert "host-neutral ok" in capsys.readouterr().out


def _write(root: Path, rel: str, content: str | bytes) -> str:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content)
    return rel


@pytest.mark.parametrize(
    "line",
    [
        "Open the Dashboard to see it",
        "DASHBOARD_HOOK_URL = 'x'",
        "def f(tracker_dir): ...",
        "stored in .tracker/simulator",
        "topic_id = 3",
        "a workspace_id argument",
        "each seat gets one",
        "the Seats page",
        "~/.dashboard/hook-secret",
    ],
)
def test_a_hosts_word_is_caught(tmp_path: Path, line: str) -> None:
    rel = _write(tmp_path, "docs/page.md", f"fine\n{line}\n")
    found = check.offenders(tmp_path, [rel])
    assert found and found[0][:2] == ("docs/page.md", 2)


def test_neutral_words_binaries_lock_files_and_the_check_itself_pass(tmp_path: Path) -> None:
    files = [
        _write(tmp_path, "src/a.py", "scope.id, xcodebuild -workspace App.xcworkspace, seating, tracker\n"),
        _write(tmp_path, "media/hero.gif", b"GIF89a\xff\xfedashboard"),
        _write(tmp_path, "uv.lock", "name = 'dashboard-kit'\n"),
        _write(tmp_path, "scripts/check_host_neutral.py", "dashboard\n"),
    ]
    assert check.offenders(tmp_path, files) == []


def test_it_names_each_offence_when_it_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, "README.md", "Works in the Dashboard\n")
    monkeypatch.setattr(check, "repo_files", lambda root: ["README.md"])
    assert check.main(tmp_path) == 1
    err = capsys.readouterr().err
    assert "README.md:1: Dashboard" in err and "HostCopy" in err
