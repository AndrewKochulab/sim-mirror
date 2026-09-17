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
    assert "1 files of helper/Sources/HelperCore at 98% or more" in capsys.readouterr().out

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


SDK = "sdk/swift/Sources/SimMirrorKit"


def _line(number: int, count: int, *unrun: int, executable: bool = True) -> dict[str, object]:
    line: dict[str, object] = {"line": number, "executionCount": count, "isExecutable": executable}
    if unrun:
        line["subranges"] = [{"column": column, "executionCount": 0, "length": 3} for column in unrun]
    return line


def test_xccov_archives_are_joined_line_by_line_and_part_by_part() -> None:
    package = {
        f"{ROOT}/{SDK}/Host.swift": [
            _line(1, 0, executable=False),
            _line(2, 5),
            _line(3, 5, 25, 70),
            _line(4, 0),
            _line(5, 2, 40),
        ],
        f"{ROOT}/{SDK}/Empty.swift": [_line(1, 0, executable=False)],
        f"{ROOT}/sdk/swift/Tests/SimMirrorKitTests/HostTests.swift": [_line(1, 0)],
    }
    app = {
        f"{ROOT}/{SDK}/Host.swift": [_line(2, 1), _line(3, 1, 70), _line(4, 3), _line(5, 0)],
        f"{ROOT}/examples/app-sdk/AppSDK/App.swift": [_line(1, 0)],
    }
    assert check.xccov_coverage([package, app], f"/{SDK}/") == {
        f"{SDK}/Host.swift": 50.0,
        f"{SDK}/Empty.swift": 100.0,
    }
    assert check.xccov_coverage([package], f"/{SDK}/")[f"{SDK}/Host.swift"] == 25.0


def test_xccov_archives_pass_fail_and_a_report_of_neither_kind_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    covered = tmp_path / "covered.json"
    covered.write_text(json.dumps({f"{ROOT}/{SDK}/A.swift": [_line(1, 1)]}))
    also = tmp_path / "also.json"
    also.write_text(json.dumps({f"{ROOT}/{SDK}/B.swift": [_line(1, 1)]}))
    assert check.main(["--min", "98", "--under", f"{SDK}/", str(covered), str(also)]) == 0
    assert f"2 files of {SDK} at 98% or more" in capsys.readouterr().out

    low = tmp_path / "low.json"
    low.write_text(json.dumps({f"{ROOT}/{SDK}/Low.swift": [_line(1, 1), _line(2, 0)]}))
    assert check.main(["--min", "98", "--under", SDK, str(low)]) == 1
    assert f"50.00%  {SDK}/Low.swift" in capsys.readouterr().err

    neither = tmp_path / "neither.json"
    neither.write_text(json.dumps({"a": 1}))
    assert check.main(["--min", "98", "--under", SDK, str(neither), str(covered)]) == 2
    assert "neither llvm-cov's export nor xccov's archive" in capsys.readouterr().err
    assert check.main(["--min", "98", str(covered)]) == 2
    assert "measures no file of helper/Sources/HelperCore" in capsys.readouterr().err
