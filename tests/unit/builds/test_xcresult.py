# SPDX-License-Identifier: Apache-2.0
"""What a build or a test run said, from real result bundles: the verdict, and each problem where it is."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sim_mirror.build.xcresult import (
    LISTED_MAX,
    MESSAGE_MAX,
    Failure,
    Flaky,
    build_summary,
    issue_location,
    render_build,
    render_tests,
    suite_summary,
)
from sim_mirror.testing.fakes import fixture_json

ROOT = Path("/Users/dev/NotesProbe")


def test_a_clean_build_says_so_in_one_line() -> None:
    summary = build_summary(fixture_json("xcresult-build-ok.json"))
    assert summary.succeeded and summary.error_count == 0 and summary.seconds == 6.9
    assert render_build(summary, label="NotesProbe (Debug)", root=ROOT) == [
        "build ok · NotesProbe (Debug) · 6.9s · 0 errors, 0 warnings"
    ]


def test_a_broken_build_says_what_broke_where_counting_lines_as_an_editor_does() -> None:
    summary = build_summary(fixture_json("xcresult-build-broken.json"))
    assert not summary.succeeded and summary.seconds == 1.5
    assert render_build(summary, label="NotesProbe (Debug)", root=ROOT) == [
        "build FAILED · NotesProbe (Debug) · 1.5s · 1 error, 0 warnings",
        "error Sources/NotesApp.swift:39:19 Cannot convert value of type 'String' to specified type 'Int'",
    ]
    outside = render_build(summary, label="NotesProbe (Debug)", root=Path("/elsewhere"))
    assert outside[1].startswith("error /Users/dev/NotesProbe/Sources/NotesApp.swift:39:19 ")


def test_a_path_through_a_symlink_is_still_shown_inside_the_project_folder(tmp_path: Path) -> None:
    real = tmp_path / "real"
    (real / "Sources").mkdir(parents=True)
    (tmp_path / "link").symlink_to(real)
    url = f"file://{tmp_path}/link/Sources/App.swift#StartingLineNumber=2&StartingColumnNumber=4"
    summary = build_summary(
        {"status": "failed", "errorCount": 1, "errors": [{"message": "Cannot find 'x' in scope", "sourceURL": url}]}
    )
    assert (
        render_build(summary, label="App (Debug)", root=real)[1]
        == "error Sources/App.swift:3:5 Cannot find 'x' in scope"
    )


def test_a_path_that_cannot_be_resolved_or_has_no_folder_to_be_inside_is_shown_as_it_came(tmp_path: Path) -> None:
    (tmp_path / "loop").symlink_to(tmp_path / "loop")
    looping = build_summary(
        {
            "status": "failed",
            "errorCount": 1,
            "errors": [{"message": "boom", "sourceURL": f"file://{tmp_path}/loop/App.swift#StartingLineNumber=0"}],
        }
    )
    assert render_build(looping, label="App (Debug)", root=tmp_path)[1].startswith("error ")
    no_folder = build_summary(
        {
            "status": "failed",
            "errorCount": 1,
            "errors": [{"message": "boom", "sourceURL": "file:///Users/dev/NotesProbe/A.swift#StartingLineNumber=0"}],
        }
    )
    assert render_build(no_folder, label="App (Debug)", root=None)[1] == "error /Users/dev/NotesProbe/A.swift:1 boom"


def test_a_path_the_system_refuses_to_resolve_is_compared_as_it_came(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(self: Path, strict: bool = False) -> Path:
        raise OSError("Operation not permitted")

    monkeypatch.setattr(Path, "resolve", refuse)
    url = "file:///Users/dev/NotesProbe/Sources/A.swift#StartingLineNumber=0"
    summary = build_summary({"status": "failed", "errorCount": 1, "errors": [{"message": "boom", "sourceURL": url}]})
    assert render_build(summary, label="App (Debug)", root=ROOT)[1] == "error Sources/A.swift:1 boom"


def test_files_a_failed_compile_did_not_produce_are_counted_not_listed_beside_the_error_that_explains_them() -> None:
    compile_error = {
        "message": "Cannot convert value of type 'String' to specified type 'Int'",
        "sourceURL": "file:///Users/dev/NotesProbe/Sources/NotesApp.swift#StartingLineNumber=14",
    }
    # Xcode's own words, curly quotes and all.
    not_produced: list[dict[str, Any]] = [
        {"message": f"The file “NotesProbe.{suffix}” couldn’t be opened because there is no such file."}
        for suffix in ("swiftdoc", "swiftmodule", "abi.json", "swiftsourceinfo")
    ]
    summary = build_summary({"status": "failed", "errorCount": 5, "errors": [compile_error, *not_produced]})
    assert render_build(summary, label="NotesProbe (Debug)", root=ROOT) == [
        "build FAILED · NotesProbe (Debug) · 5 errors, 0 warnings",
        "error Sources/NotesApp.swift:15 Cannot convert value of type 'String' to specified type 'Int'",
        "… 4 follow-on errors from that failure: files it did not produce",
    ]
    # With no located error to explain them, and for any other error without a place, every line is listed.
    alone = build_summary(
        {"status": "failed", "errorCount": 2, "errors": [not_produced[0], {"message": "Undefined symbol: _main"}]}
    )
    assert render_build(alone, label="App (Debug)", root=ROOT)[1:] == [
        f"error {not_produced[0]['message']}",
        "error Undefined symbol: _main",
    ]


def test_warnings_are_listed_only_when_asked_and_what_is_not_listed_is_counted() -> None:
    many = [
        {"message": f"problem {n}", "sourceURL": f"file:///Users/dev/NotesProbe/A.swift#StartingLineNumber={n}"}
        for n in range(LISTED_MAX + 5)
    ]
    summary = build_summary(
        {
            "status": "failed",
            "errors": many,
            "errorCount": LISTED_MAX + 5,
            "warnings": [{"message": "unused variable 'x'"}],
            "warningCount": 1,
        }
    )
    lines = render_build(summary, label="App (Debug)", root=ROOT)
    assert lines[0] == "build FAILED · App (Debug) · 25 errors, 1 warning"
    assert lines[1] == "error A.swift:1 problem 0" and lines[-1] == "… 5 more not listed"
    assert len(lines) == LISTED_MAX + 2
    with_warnings = render_build(summary, label="App (Debug)", root=ROOT, warnings=True)
    assert "warning unused variable 'x'" in with_warnings and with_warnings[-1] == "… 5 more not listed"


def test_a_build_document_missing_its_counts_and_times_is_read_as_far_as_it_goes() -> None:
    summary = build_summary(
        {"status": "succeeded", "errors": "not a list", "errorCount": True, "startTime": 5, "endTime": 2}
    )
    assert summary.error_count == 0 and summary.seconds is None and summary.errors == ()
    assert render_build(summary, label="App (Debug)", root=None) == ["build ok · App (Debug) · 0 errors, 0 warnings"]
    long = build_summary({"status": "failed", "errors": [{"message": "x" * 1000}, "not an entry"]})
    assert len(long.errors) == 1 and len(long.errors[0].message) == MESSAGE_MAX and long.errors[0].file is None


def test_a_source_location_is_a_path_a_line_and_a_column_counted_from_one() -> None:
    assert issue_location("file:///Users/dev/My%20App/A.swift#StartingLineNumber=0&StartingColumnNumber=4") == (
        "/Users/dev/My App/A.swift",
        1,
        5,
    )
    assert issue_location("file:///Users/dev/A.swift") == ("/Users/dev/A.swift", None, None)
    assert issue_location("https://example.com/A.swift") == (None, None, None)
    assert issue_location(None) == (None, None, None)


def test_a_test_run_says_how_many_passed_and_where_each_failure_is() -> None:
    summary = suite_summary(fixture_json("xcresult-test-summary.json"), fixture_json("xcresult-test-tests.json"))
    assert (summary.passed, summary.passed_count, summary.failed_count, summary.skipped_count) == (False, 2, 1, 0)
    lines = render_tests(summary, label="NotesProbe (Debug)")
    assert lines[0] == "test FAILED · NotesProbe (Debug) · 2 passed, 1 failed, 0 skipped · 34.3s"
    assert lines[1].startswith(
        'fail NotesTests/testDeliberatelyFails() NotesTests.swift:10 XCTAssertEqual failed: ("Trip") is not equal to '
        '("Tripp")'
    )
    assert len(lines) == 2


def test_a_failure_without_its_tree_is_still_listed_and_more_than_are_listed_are_counted() -> None:
    summary = suite_summary(
        {
            "result": "Failed",
            "passedTests": 0,
            "failedTests": 3,
            "skippedTests": 1,
            "testFailures": [
                {"testName": "testOne()", "failureText": "A.swift:3: boom"},
                {"failureText": "no name"},
                "not a failure",
            ],
        },
        {
            "testNodes": [
                {
                    "nodeType": "Test Case",
                    "nodeIdentifier": "Suite/testOne()",
                    "children": ["odd", {"nodeType": "Attachment", "name": "B.swift:9: x"}],
                },
                "not a node",
            ]
        },
    )
    assert render_tests(summary, label="App (Debug)") == [
        "test FAILED · App (Debug) · 0 passed, 3 failed, 1 skipped",
        "fail testOne() boom",
        "fail a test no name",
        "… 1 more failures not listed",
    ]
    passing = suite_summary({"result": "Passed", "passedTests": 4, "testFailures": None})
    assert render_tests(passing, label="App (Debug)") == ["test ok · App (Debug) · 4 passed, 0 failed, 0 skipped"]
    assert suite_summary({"result": "Passed"}, None).failures == ()


def test_a_retried_run_says_how_many_attempts_a_failure_had_and_names_a_test_that_only_passed_on_one() -> None:
    """Read from a real bundle: the sample app run with `-retry-tests-on-failure -test-iterations 3`, with a test
    that fails the first time and passes the second (`xcresult-test-*-retries.json`, Xcode 26.6).

    The flaky line is the point. That test is counted among the **passed** and appears nowhere in `testFailures`, so
    a run that needed two goes reads as a clean pass unless this says otherwise.
    """
    summary = suite_summary(
        fixture_json("xcresult-test-summary-retries.json"), fixture_json("xcresult-test-tests-retries.json")
    )
    assert (summary.passed_count, summary.failed_count, summary.expected_failures) == (3, 1, 1)
    assert summary.flaky == (Flaky("NotesTests/testFlakyOnFirstTry()", 2),)
    assert summary.failures[0].attempts == 3
    lines = render_tests(summary, label="NotesProbe (Debug)")
    assert lines[0] == "test FAILED · NotesProbe (Debug) · 3 passed, 1 failed, 0 skipped, 1 failed as expected · 64.8s"
    assert lines[1].startswith("fail NotesTests/testDeliberatelyFails() NotesTests.swift:10 XCTAssertEqual failed")
    assert lines[1].endswith("(failed 3 times)")
    assert lines[2] == "flaky NotesTests/testFlakyOnFirstTry() failed, then passed on attempt 2"
    assert len(lines) == 3


def test_a_run_that_retried_nothing_says_nothing_about_attempts() -> None:
    """The ordinary run, from the fixture taken before retries were asked for: one attempt each, nothing flaky."""
    summary = suite_summary(fixture_json("xcresult-test-summary.json"), fixture_json("xcresult-test-tests.json"))
    assert summary.flaky == () and summary.failures[0].attempts == 1
    assert all("attempt" not in line and "flaky" not in line for line in render_tests(summary, label="App (Debug)"))


def test_a_failure_whose_place_cannot_be_read_is_still_reported() -> None:
    """A failure message without a `File.swift:12:` in front of it, a repetition holding no failure at all, and a
    node whose children are not a list: each leaves the place unknown rather than losing the failure."""
    tests = {"testNodes": [{
        "nodeType": "Test Case", "nodeIdentifier": "S/testOdd()", "result": "Failed",
        "children": [
            {"nodeType": "Failure Message", "name": "no location in this one"},
            {"nodeType": "Repetition", "name": "First Run", "result": "Failed", "children": "not a list"},
            {"nodeType": "Repetition", "name": "Retry 1", "result": "Failed", "children": []},
        ],
    }]}  # fmt: skip
    summary = suite_summary(
        {"result": "Failed", "failedTests": 1, "testFailures": [{"testIdentifierString": "S/testOdd()",
                                                                 "failureText": "it broke"}]}, tests)  # fmt: skip
    assert summary.failures == (Failure("S/testOdd()", "it broke", None, None, 2),)
    assert render_tests(summary, label="App (Debug)")[1] == "fail S/testOdd() it broke (failed 2 times)"


def test_a_test_that_failed_every_attempt_is_not_called_flaky() -> None:
    """Flaky means it passed in the end. One that never passed is simply a failure, and saying otherwise would
    tell an agent to re-run something that will fail again."""
    tests = {"testNodes": [{
        "nodeType": "Test Case", "nodeIdentifier": "S/testAlways()", "result": "Failed",
        "children": [
            {"nodeType": "Repetition", "name": "First Run", "result": "Failed"},
            {"nodeType": "Repetition", "name": "Retry 1", "result": "Failed"},
        ],
    }]}  # fmt: skip
    assert suite_summary({"result": "Failed", "failedTests": 1}, tests).flaky == ()


def test_tests_that_failed_on_purpose_are_counted_apart_and_only_mentioned_when_there_are_any() -> None:
    """Without them the three counts do not add up to the tests there were, and a passing run reads as having lost
    one. The fixture from a real run has none, so a run that has them says so and the usual line is unchanged."""
    assert suite_summary(fixture_json("xcresult-test-summary.json")).expected_failures == 0
    expecting = suite_summary({"result": "Passed", "passedTests": 3, "expectedFailures": 2, "totalTestCount": 5})
    assert expecting.expected_failures == 2
    assert render_tests(expecting, label="App (Debug)") == [
        "test ok · App (Debug) · 3 passed, 0 failed, 0 skipped, 2 failed as expected"
    ]
