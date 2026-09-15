# SPDX-License-Identifier: Apache-2.0
"""The per-file coverage gate: every file on its own, the least covered named first."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import check_per_file_coverage as check


def _report(**percents: float) -> dict[str, object]:
    return {"files": {name: {"summary": {"percent_covered": percent}} for name, percent in percents.items()}}


def test_files_under_the_minimum_are_named_least_covered_first() -> None:
    report = _report(a=100.0, b=97.5, c=60.0, d=98.0)
    assert check.shortfalls(report, 98) == [("c", 60.0), ("b", 97.5)]
    assert check.shortfalls({}, 98) == []


def test_it_passes_fails_and_refuses_an_unreadable_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    good = tmp_path / "good.json"
    good.write_text(json.dumps(_report(a=100.0, b=99.0)))
    assert check.main(["--min", "98", str(good)]) == 0
    assert "2 files at 98% or more" in capsys.readouterr().out

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(_report(a=100.0, low=91.25)))
    assert check.main([str(bad), "--min", "98"]) == 1
    assert "91.25%  low" in capsys.readouterr().err

    assert check.main(["--min", "98", str(tmp_path / "missing.json")]) == 2
    (tmp_path / "broken.json").write_text("{")
    assert check.main(["--min", "98", str(tmp_path / "broken.json")]) == 2
    assert "cannot read the coverage report" in capsys.readouterr().err
