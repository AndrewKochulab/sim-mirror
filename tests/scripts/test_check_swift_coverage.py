# SPDX-License-Identifier: Apache-2.0
"""The native helper's coverage gate: every file of its core on its own, by lines; its platform layer is not counted."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import check_swift_coverage as check

ROOT = "/Users/me/sim-mirror"


def _report(**files: tuple[int, int]) -> dict[str, object]:
    entries = [
        {"filename": f"{ROOT}/{name}", "summary": {"lines": {"count": count, "covered": covered}}}
        for name, (count, covered) in files.items()
    ]
    return {"data": [{"files": entries}]}


def test_only_the_core_is_measured_and_an_empty_file_is_covered() -> None:
    report = _report(
        **{
            "helper/Sources/HelperCore/Wire.swift": (200, 199),
            "helper/Sources/HelperCore/Empty.swift": (0, 0),
            "helper/Sources/HelperPlatform/Framebuffer.swift": (100, 0),
            "helper/Tests/HelperCoreTests/WireTests.swift": (50, 50),
        }
    )
    assert check.coverage(report) == {
        "helper/Sources/HelperCore/Wire.swift": 99.5,
        "helper/Sources/HelperCore/Empty.swift": 100.0,
    }
    assert check.coverage({}) == {}


def test_it_passes_fails_and_refuses_what_it_cannot_read(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    good = tmp_path / "good.json"
    good.write_text(json.dumps(_report(**{"helper/Sources/HelperCore/A.swift": (100, 98)})))
    assert check.main(["--min", "98", str(good)]) == 0
    assert "1 files at 98% or more" in capsys.readouterr().out

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(_report(**{"helper/Sources/HelperCore/Low.swift": (400, 365)})))
    assert check.main([str(bad), "--min", "98"]) == 1
    assert "91.25%  helper/Sources/HelperCore/Low.swift" in capsys.readouterr().err

    none = tmp_path / "none.json"
    none.write_text(json.dumps(_report(**{"helper/Sources/HelperPlatform/A.swift": (10, 0)})))
    assert check.main(["--min", "98", str(none)]) == 2
    assert "measures no file of helper/Sources/HelperCore" in capsys.readouterr().err

    (tmp_path / "broken.json").write_text("{")
    assert check.main(["--min", "98", str(tmp_path / "broken.json")]) == 2
    assert check.main(["--min", "98", str(tmp_path / "missing.json")]) == 2
    assert "cannot read the coverage report" in capsys.readouterr().err
