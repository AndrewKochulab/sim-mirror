# SPDX-License-Identifier: Apache-2.0
"""Skill artifacts remain portable and bounded; instructions' behavior is evaluated separately."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

import check_skills as skills


def populated(root: Path) -> Path:
    for name in skills.NAMES:
        path = root / skills.SKILLS / name / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text(f"---\nname: {name}\ndescription: Use for this workflow.\n---\n\nsim_snapshot\n")
    return root / skills.SKILLS / "simulator"


def test_shipped_skills_validate_and_archive_as_standalone_folders(tmp_path: Path) -> None:
    target = tmp_path / "dist" / "skills.zip"
    assert skills.main(["--output", str(target)]) == 0
    with zipfile.ZipFile(target) as archive:
        archive.extractall(tmp_path / "unpacked" / skills.SKILLS)
    assert skills.problems(tmp_path / "unpacked") == []
    before = target.read_bytes()
    assert skills.main(["--output", str(target)]) == 0
    assert target.read_bytes() == before


def test_reference_must_be_bundled_and_directly_discoverable(tmp_path: Path) -> None:
    folder = populated(tmp_path)
    entry = folder / "SKILL.md"
    ref = folder / "references" / "phone.md"
    ref.parent.mkdir()
    ref.write_text("Use sim_device.\n")
    assert "link directly" in skills.problems(tmp_path)[0]
    entry.write_text(entry.read_text() + "\n[phone](references/phone.md) [docs](https://example.com)\n")
    assert skills.problems(tmp_path) == []
    entry.write_text(entry.read_text() + "[outside](../../../../README.md) [missing](gone.md)\n")
    assert len(skills.problems(tmp_path)) == 2


def test_non_markdown_references_cannot_silently_disappear_from_the_archive(tmp_path: Path) -> None:
    folder = populated(tmp_path)
    (folder / "data.json").write_text("{}")
    entry = folder / "SKILL.md"
    entry.write_text(entry.read_text() + "[data](data.json)\n")
    assert "not bundled" in skills.problems(tmp_path)[0]


@pytest.mark.parametrize("bad", ["sim_nonexistent", "word " * 501, "---\nname: wrong\n---\n"])
def test_invalid_skill_fails_before_packaging(tmp_path: Path, bad: str) -> None:
    folder = populated(tmp_path)
    (folder / "SKILL.md").write_text(bad)
    target = tmp_path / "bad.zip"
    assert skills.main(["--output", str(target)], root=tmp_path) == 1
    assert not target.exists()


def test_missing_skill_is_named_and_validation_without_packaging_works(tmp_path: Path) -> None:
    assert skills.problems(tmp_path)
    populated(tmp_path)
    assert skills.main([], root=tmp_path) == 0
