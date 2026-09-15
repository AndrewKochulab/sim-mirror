# SPDX-License-Identifier: Apache-2.0
"""The containment guard, both ways: the repository is clean, and the shapes it exists to catch are caught."""

from __future__ import annotations

from pathlib import Path

import pytest

import _containment
import check_containment as guard


def test_the_repository_is_clean(capsys: pytest.CaptureFixture[str]) -> None:
    found = guard.offenders()
    assert found == [], "\n".join(f"{f}:{n}: {p}" for f, n, p in found)
    assert guard.main() == 0
    assert "containment ok" in capsys.readouterr().out


def _scan(tmp_path: Path, source: str, rel: str = "src/sim_mirror/core/sample.py") -> list[tuple[str, int, str]]:
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
    return _containment.offenders(tmp_path, guard.SCAN_DIRS, guard.ALLOWED, guard.PROGRAMS)


def test_it_catches_a_command_started_with_any_of_them(tmp_path: Path) -> None:
    source = (
        'subprocess.run(["xcrun", "simctl", "boot", udid])\n'
        'asyncio.create_subprocess_exec("/opt/homebrew/bin/idb_companion", "--udid", u)\n'
        'ARGV = ("xcodebuild", "-scheme", s)\n'
        'CMD = "xcresulttool get build-results"\n'
        'await runner("simctl", "list")\n'
    )
    found = _scan(tmp_path, source)
    assert [(line, program) for _f, line, program in found] == [
        (1, "xcrun"),
        (2, "idb_companion"),
        (3, "xcodebuild"),
        (4, "xcresulttool"),
        (5, "simctl"),
    ]


def test_a_name_that_starts_no_command_is_not_its_business(tmp_path: Path) -> None:
    source = (
        'CONNECTORS = ("auto", "idb", "simctl")\n'
        'Kind = Literal["idb", "simctl"]\n'
        'name = "simctl"\n'
        'label = "the idb_companion program"\n'
        "empty = ()\n"
        'blank = run(" ")\n'
        "call()\n"
    )
    assert _scan(tmp_path, source) == []


def test_the_owners_and_prose_may_name_them(tmp_path: Path) -> None:
    assert _scan(tmp_path, 'run(["xcrun"])\n', "src/sim_mirror/platform/xcrun.py") == []
    assert _scan(tmp_path, 'run(["simctl"])\n', "src/sim_mirror/connectors/simctl/capture.py") == []
    assert _scan(tmp_path, 'run(["idb_companion"])\n', "src/sim_mirror/connectors/idb/companion.py") == []
    assert _scan(tmp_path, 'fake.on("simctl", "list")\n', "src/sim_mirror/testing/fakes.py") == []
    source = '"""Boots with xcrun simctl, in prose."""\ndef f():\n    """idb_companion too."""\n    run(["ls"])\n'
    assert _scan(tmp_path, source) == []


def test_unreadable_files_folders_that_do_not_exist_and_caches_are_skipped(tmp_path: Path) -> None:
    assert _scan(tmp_path, "def broken(:\n") == []
    assert _scan(tmp_path, 'run(["xcrun"])\n', "src/sim_mirror/__pycache__/x.py") == []
    (tmp_path / "src" / "sim_mirror" / "latin1.py").write_bytes(b'X = "caf\xe9"\n')
    assert _containment.offenders(tmp_path, ("src", "missing"), guard.ALLOWED, guard.PROGRAMS) == []


def test_allowances_match_files_and_folders_only() -> None:
    assert _containment.allowed("a/b.py", ["a/b.py"])
    assert _containment.allowed("a/b/c.py", ["a/b/"])
    assert not _containment.allowed("a/bc.py", ["a/b"])


def test_it_says_where_to_go_when_it_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _scan(tmp_path, 'run("simctl")\n')
    monkeypatch.setattr(guard, "REPO_ROOT", tmp_path)
    assert guard.main() == 1
    err = capsys.readouterr().err
    assert "src/sim_mirror/core/sample.py:1: simctl" in err and "sim_mirror/platform/" in err
