# SPDX-License-Identifier: Apache-2.0
"""The wheel check: every wheel carries the helper's sources, a release wheel the executable universal helper."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from check_wheel import SOURCES, UNIVERSAL_MAGIC, main, problems, wheel_tags
from hatch_build import BINARY_TARGET, MAC_TAG

WHEEL_METADATA = "sim_mirror-1.0.0.dist-info/WHEEL"


def wheel(
    path: Path,
    *,
    sources: bool = True,
    binary: bytes | None = UNIVERSAL_MAGIC + b"slices",
    mode: int = 0o755,
    tag: str | None = MAC_TAG,
) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        if sources:
            for name in SOURCES:
                archive.writestr(name, "// source\n")
        if binary is not None:
            info = zipfile.ZipInfo(BINARY_TARGET)
            info.external_attr = (0o100000 | mode) << 16
            archive.writestr(info, binary)
        if tag is not None:
            archive.writestr(WHEEL_METADATA, f"Wheel-Version: 1.0\nRoot-Is-Purelib: false\nTag: {tag}\n")
    return path


def test_a_release_wheel_is_ok(tmp_path: Path) -> None:
    assert problems(wheel(tmp_path / "w.whl"), helper=True) == []


def test_a_pure_wheel_needs_only_the_sources(tmp_path: Path) -> None:
    assert problems(wheel(tmp_path / "w.whl", binary=None, tag="py3-none-any"), helper=False) == []


def test_missing_sources_are_named(tmp_path: Path) -> None:
    found = problems(wheel(tmp_path / "w.whl", sources=False), helper=False)
    assert found == [f"w.whl: the helper's sources are missing {name}" for name in SOURCES]


def test_a_release_wheel_without_the_helper(tmp_path: Path) -> None:
    assert problems(wheel(tmp_path / "w.whl", binary=None), helper=True) == [
        f"w.whl: carries no helper at {BINARY_TARGET}"
    ]


def test_a_helper_that_cannot_run_on_both_architectures_or_at_all(tmp_path: Path) -> None:
    found = problems(wheel(tmp_path / "w.whl", binary=b"\xcf\xfa\xed\xfe", mode=0o644, tag="py3-none-any"), helper=True)
    assert found == [
        f"w.whl: {BINARY_TARGET} is not executable",
        f"w.whl: {BINARY_TARGET} is not a universal binary for both Mac architectures",
        f"w.whl: is tagged ['py3-none-any'], not ['{MAC_TAG}']",
    ]


def test_a_wheel_without_metadata_has_no_tags(tmp_path: Path) -> None:
    with zipfile.ZipFile(wheel(tmp_path / "w.whl", tag=None)) as archive:
        assert wheel_tags(archive) == []


def test_what_is_not_a_wheel(tmp_path: Path) -> None:
    (tmp_path / "w.whl").write_text("not a zip")
    assert problems(tmp_path / "w.whl", helper=False)[0].startswith(f"{tmp_path / 'w.whl'}: not a wheel")


def test_main_reports_ok(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--helper", str(wheel(tmp_path / "w.whl"))]) == 0
    assert capsys.readouterr().out == "wheel ok: 1 wheel(s) carry the helper and its sources\n"
    assert main([str(wheel(tmp_path / "p.whl", binary=None))]) == 0
    assert capsys.readouterr().out == "wheel ok: 1 wheel(s) carry the helper's sources\n"


def test_main_lists_each_problem(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--helper", str(wheel(tmp_path / "w.whl", binary=None))]) == 1
    err = capsys.readouterr().err
    assert "does not carry the native helper" in err
    assert f"  w.whl: carries no helper at {BINARY_TARGET}" in err
