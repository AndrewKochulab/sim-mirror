# SPDX-License-Identifier: Apache-2.0
"""Release notes come from the version's CHANGELOG.md section, and a version without one stops the release."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from release_notes import CHANGELOG, NoNotes, main, notes

CHANGES = """# Changelog

Intro.

## [Unreleased]

## [0.2.0] - 2026-10-01

### Added

- Android.

## [0.1.0] - 2026-09-20

### Added

- The viewer.
"""


def test_a_version_s_section_is_its_notes() -> None:
    assert notes(CHANGES, "0.2.0") == "### Added\n\n- Android.\n"
    assert notes(CHANGES, "0.1.0") == "### Added\n\n- The viewer.\n"


def test_a_version_without_notes_is_refused() -> None:
    with pytest.raises(NoNotes, match=r"no section for 9\.9\.9: add `## \[9\.9\.9\] - YYYY-MM-DD`"):
        notes(CHANGES, "9.9.9")
    with pytest.raises(NoNotes, match="section for Unreleased is empty"):
        notes(CHANGES, "Unreleased")


def test_main_takes_a_tag_and_prints_the_notes(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGES, encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()
    assert main(["v0.1.0"], changelog=changelog, out=out, err=err) == 0
    assert out.getvalue() == "### Added\n\n- The viewer.\n" and err.getvalue() == ""
    assert main(["0.3.0"], changelog=changelog, out=out, err=err) == 1
    assert "no section for 0.3.0" in err.getvalue()


def test_main_defaults_to_the_standard_streams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGES, encoding="utf-8")
    assert main(["0.2.0"], changelog=changelog) == 0
    assert main(["1.0.0"], changelog=changelog) == 1
    captured = capsys.readouterr()
    assert "- Android." in captured.out and "no section for 1.0.0" in captured.err
    assert CHANGELOG.name == "CHANGELOG.md"
