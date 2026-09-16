# SPDX-License-Identifier: Apache-2.0
"""What a build or a test run said, read from its result bundle, as a few lines an agent can act on.

The result bundle tool's ``get build-results`` and ``get test-results summary|tests`` answer in JSON (measured on Xcode
26.6 and 27.0; fixtures in `sim_mirror/testing/fixtures/`). This turns that into what an agent needs next, and
nothing it would have to wade through:

    build FAILED · NotesProbe (Debug) · 1.5s · 1 error, 0 warnings
    error Sources/NotesApp.swift:39:19 Cannot convert value of type 'String' to specified type 'Int'

    test FAILED · NotesProbe · 2 passed, 1 failed, 0 skipped · 34.3s
    fail NotesProbeTests/NotesTests/testDeliberatelyFails Tests/NotesTests.swift:10 XCTAssertEqual failed: ("Trip") …

JSON in, text out. A result bundle's source locations count lines and columns from zero; shown here as an editor shows
them, from one. A path inside the project folder is shown relative to it -- compared with symlinks resolved, since a
result bundle names `/tmp/…` where the folder is `/private/tmp/…`.

A compile that fails also fails the steps copying what it would have produced, each an error with no location ('The
file "App.swiftmodule" could not be opened because there is no such file', in Xcode's curly quotes). Beside a located
error those say nothing an agent can act on, so they are counted in one line rather than listed.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

#: The most errors, warnings or failures listed; the counts still say how many there were.
LISTED_MAX = 20
MESSAGE_MAX = 300

_FAILURE_AT = re.compile(r"\A(?P<file>[^:\s]+\.[A-Za-z]+):(?P<line>\d+): ")
#: Where a result bundle's test URLs start: ``test://com.apple.xcode/<container>/<target>/<suite>/<test>``.
_TEST_URL = "test://com.apple.xcode/"
#: What a test run's build adds when its own failure stopped the tests; the answer's first line says that instead.
_TESTING_CANCELLED = re.compile(r"\ATesting cancelled because the build failed")
#: The error a copy step gives for a file a failed compile never produced.
_NOT_PRODUCED = re.compile(r"couldn.t be opened because there is no such file")


def _resolved(path: Path) -> Path:
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        return path


def shown_path(file: str, root: Path | None) -> str:
    """A path inside the project folder relative to it, compared with symlinks resolved; any other as it came."""
    path = Path(file)
    if root is not None and path.is_absolute():
        resolved, base = _resolved(path), _resolved(root)
        if resolved.is_relative_to(base):
            return str(resolved.relative_to(base))
    return file


def _short(text: object) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= MESSAGE_MAX else value[: MESSAGE_MAX - 1] + "…"


def _seconds(start: object, end: object) -> float | None:
    if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end >= start:
        return round(end - start, 1)
    return None


@dataclass(frozen=True)
class Issue:
    kind: str
    message: str
    file: str | None = None
    line: int | None = None
    column: int | None = None

    def where(self, root: Path | None) -> str:
        if self.file is None:
            return ""
        path = shown_path(self.file, root)
        return path + (f":{self.line}" if self.line else "") + (f":{self.column}" if self.column else "")


@dataclass(frozen=True)
class BuildSummary:
    succeeded: bool
    errors: tuple[Issue, ...]
    warnings: tuple[Issue, ...]
    error_count: int
    warning_count: int
    seconds: float | None


@dataclass(frozen=True)
class Failure:
    test: str
    message: str
    file: str | None = None
    line: int | None = None
    #: How many times it was run, when `-retry-tests-on-failure` ran it more than once. 1 when it was run once.
    attempts: int = 1


@dataclass(frozen=True)
class Flaky:
    """A test that passed, but only after failing.

    The summary counts it among the passed and leaves it out of `testFailures` altogether -- measured on Xcode 26.6,
    where a test that failed once and passed on retry is simply "passed". So an agent is told a suite passed and
    never learns that a test needed two goes, which is exactly the thing worth knowing: a fix that looks confirmed
    may only have been lucky.
    """

    test: str
    attempts: int


@dataclass(frozen=True)
class SuiteSummary:
    passed: bool
    passed_count: int
    failed_count: int
    skipped_count: int
    failures: tuple[Failure, ...]
    seconds: float | None
    #: Tests that failed on purpose (`XCTExpectFailure`). Counted apart, or the other three would not add up to the
    #: tests there were, and an agent would read a passing run as having lost a test.
    expected_failures: int = 0
    #: Tests that passed only after failing. Empty unless the run retried, which needs `-retry-tests-on-failure`.
    flaky: tuple[Flaky, ...] = ()


def runnable_id(url: object, identifier: str, target: object = None) -> str:
    """A test as `-only-testing` names it -- target, suite and test: ``ProbeTests/TripTests/testSplitsTrips`` or
    ``ProbeTests/ParsingTests/countsTrips()`` -- so an agent can run again exactly what failed.

    The test's URL is the one place a result bundle says all three (measured on Xcode 26.6 and 27.0): its
    ``testIdentifierString`` leaves the target out, and so does the tree's ``nodeIdentifier``. The URL gives an XCTest
    method without ``()`` and a Swift Testing function with them, which is how xcodebuild takes each back. Without
    a URL, the target is put in front of the identifier when it is known.
    """
    if isinstance(url, str) and url.startswith(_TEST_URL):
        parts = [unquote(part) for part in url[len(_TEST_URL) :].split("/")]
        if len(parts) >= 3 and all(parts):
            return "/".join(parts[1:])
    return f"{target}/{identifier}" if isinstance(target, str) and target else identifier


def issue_location(source_url: object) -> tuple[str | None, int | None, int | None]:
    """A result bundle's ``file:///…#StartingLineNumber=…`` as a path, a line and a column counted from one."""
    if not isinstance(source_url, str) or not source_url.startswith("file://"):
        return None, None, None
    parts = urlsplit(source_url)
    fragment = parse_qs(parts.fragment)

    def number(key: str) -> int | None:
        raw = (fragment.get(key) or [""])[0]
        return int(raw) + 1 if raw.isdigit() else None

    return unquote(parts.path) or None, number("StartingLineNumber"), number("StartingColumnNumber")


def _issues(entries: object, kind: str) -> tuple[Issue, ...]:
    issues = []
    for entry in (entries if isinstance(entries, list) else [])[:LISTED_MAX]:
        if isinstance(entry, dict):
            file, line, column = issue_location(entry.get("sourceURL"))
            issues.append(Issue(kind, _short(entry.get("message")), file, line, column))
    return tuple(issues)


def _count(value: object, fallback: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else fallback


def build_summary(document: dict[str, Any]) -> BuildSummary:
    errors = _issues(document.get("errors"), "error")
    warnings = _issues(document.get("warnings"), "warning")
    return BuildSummary(
        succeeded=document.get("status") == "succeeded",
        errors=errors,
        warnings=warnings,
        error_count=_count(document.get("errorCount"), len(errors)),
        warning_count=_count(document.get("warningCount"), len(warnings)),
        seconds=_seconds(document.get("startTime"), document.get("endTime")),
    )


def _failure_at(message: dict[str, Any]) -> tuple[str, int] | None:
    """Where one failure message says it happened.

    Xcode 27.0 gives a ``sourceLocation`` -- the file's whole path and a line counted from one -- and leaves the place
    out of the message; Xcode 26.6 starts the message with it, ``TripTests.swift:6: …``, the file named bare.
    """
    location = message.get("sourceLocation")
    if isinstance(location, dict):
        path, line = location.get("filePath"), location.get("lineNumber")
        if isinstance(path, str) and path and isinstance(line, int) and not isinstance(line, bool) and line > 0:
            return path, line
    match = _FAILURE_AT.match(str(message.get("name") or ""))
    return (match.group("file"), int(match.group("line"))) if match else None


def _first_failure(children: object) -> tuple[str, int] | None:
    """Where a test failed, from its own children or a repetition's: the first failure message that says."""
    for child in children if isinstance(children, list) else []:
        if not isinstance(child, dict):
            continue
        if child.get("nodeType") == "Failure Message":
            found = _failure_at(child)
            if found is not None:
                return found
        if child.get("nodeType") == "Repetition":
            found = _first_failure(child.get("children"))
            if found is not None:
                return found
    return None


def _cases(tests: object) -> dict[str, dict[str, Any]]:
    """Every test case in the tree, by identifier. A tree is small; the first of a repeated id wins."""
    found: dict[str, dict[str, Any]] = {}
    stack = list(tests.get("testNodes") or []) if isinstance(tests, dict) else []
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        identifier = node.get("nodeIdentifier")
        if node.get("nodeType") == "Test Case" and isinstance(identifier, str) and identifier not in found:
            found[identifier] = node
        stack.extend(node.get("children") or [])
    return found


def _repetitions(case: dict[str, Any]) -> list[dict[str, Any]]:
    """A test's runs, when `-retry-tests-on-failure` gave it more than one; empty when it was run once.

    Measured on Xcode 26.6: a retried test gains `Repetition` children named "First Run", "Retry 1", … each with
    its own `result`, and a test run once gains none at all.
    """
    return [
        child
        for child in case.get("children") or []
        if isinstance(child, dict) and child.get("nodeType") == "Repetition"
    ]


def _flaky(cases: dict[str, dict[str, Any]]) -> tuple[Flaky, ...]:
    """Tests that passed with a failed run behind them -- which the summary reports as simply passed."""
    found = [
        Flaky(runnable_id(case.get("nodeIdentifierURL"), identifier), len(reps))
        for identifier, case in sorted(cases.items())
        if (reps := _repetitions(case))
        and case.get("result") == "Passed"
        and any(rep.get("result") not in (None, "Passed", "Expected Failure") for rep in reps)
    ]
    return tuple(found[:LISTED_MAX])


def suite_summary(summary: dict[str, Any], tests: dict[str, Any] | None = None) -> SuiteSummary:
    cases = _cases(tests)
    failures = []
    listed = summary.get("testFailures")
    for entry in (listed if isinstance(listed, list) else [])[:LISTED_MAX]:
        if isinstance(entry, dict):
            name = str(entry.get("testIdentifierString") or entry.get("testName") or "a test")
            message = _FAILURE_AT.sub("", str(entry.get("failureText") or ""))
            case = cases.get(name, {})
            where = _first_failure(case.get("children"))
            file, line = where if where is not None else (None, None)
            shown = runnable_id(entry.get("testIdentifierURL"), name, entry.get("targetName"))
            failures.append(Failure(shown, _short(message), file, line, max(1, len(_repetitions(case)))))
    return SuiteSummary(
        passed=summary.get("result") == "Passed",
        passed_count=_count(summary.get("passedTests"), 0),
        failed_count=_count(summary.get("failedTests"), 0),
        skipped_count=_count(summary.get("skippedTests"), 0),
        failures=tuple(failures),
        seconds=_seconds(summary.get("startTime"), summary.get("finishTime")),
        expected_failures=_count(summary.get("expectedFailures"), 0),
        flaky=_flaky(cases),
    )


def placed(summary: SuiteSummary, places: Mapping[str, str]) -> SuiteSummary:
    """The summary with each failure's bare file name where the project has it (`xcodebuild.source_paths`)."""
    failures = tuple(
        replace(failure, file=places.get(failure.file, failure.file)) if failure.file else failure
        for failure in summary.failures
    )
    return replace(summary, failures=failures)


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _took(seconds: float | None) -> str:
    return f" · {seconds:g}s" if seconds is not None else ""


def any_test_ran(summary: dict[str, Any]) -> bool:
    """Whether a test run got as far as running a test.

    A run whose tests did not compile has a summary all the same -- measured on Xcode 26.6 and 27.0: ``result``
    "unknown", no tests, no failures -- and its errors are only in the build's results.
    """
    return _count(summary.get("totalTestCount"), 0) > 0


def render_build(
    summary: BuildSummary, *, label: str, root: Path | None, warnings: bool = False, kind: str = "build"
) -> list[str]:
    """The lines a build answers with: the verdict, then its errors -- and its warnings, when asked for.

    A test run whose tests did not build answers the same way, as a test run: its errors are why no test ran.
    """
    verdict = "ok" if summary.succeeded else "FAILED"
    cancelled = [error for error in summary.errors if kind == "test" and _TESTING_CANCELLED.match(error.message)]
    reported = [error for error in summary.errors if error not in cancelled]
    error_count = summary.error_count - len(cancelled)
    unbuilt = " · the tests did not build" if kind == "test" else ""
    lines = [
        f"{kind} {verdict} · {label}{unbuilt}{_took(summary.seconds)} · {_plural(error_count, 'error')}, "
        f"{_plural(summary.warning_count, 'warning')}"
    ]
    located = any(error.file for error in reported)
    follow_ons = [error for error in reported if located and not error.file and _NOT_PRODUCED.search(error.message)]
    errors = [error for error in reported if error not in follow_ons]
    shown = [*errors, *(summary.warnings if warnings else ())]
    lines += [" ".join(part for part in (issue.kind, issue.where(root), issue.message) if part) for issue in shown]
    if follow_ons:
        lines.append(f"… {_plural(len(follow_ons), 'follow-on error')} from that failure: files it did not produce")
    listed = len(reported) + (len(summary.warnings) if warnings else 0)
    hidden = error_count + (summary.warning_count if warnings else 0) - listed
    if hidden > 0:
        lines.append(f"… {hidden} more not listed")
    return lines


def render_tests(summary: SuiteSummary, *, label: str, root: Path | None = None) -> list[str]:
    """The lines a test run answers with: the verdict and counts, then each failure where it happened."""
    verdict = "ok" if summary.passed else "FAILED"
    # Said only when there are any: a run with none is every run most projects have, and the line is read every time.
    expected = f", {summary.expected_failures} failed as expected" if summary.expected_failures else ""
    lines = [
        f"test {verdict} · {label} · {summary.passed_count} passed, {summary.failed_count} failed, "
        f"{summary.skipped_count} skipped{expected}{_took(summary.seconds)}"
    ]
    for failure in summary.failures:
        where = f"{shown_path(failure.file, root)}:{failure.line}" if failure.file else ""
        # Said only when it was run more than once, so a failure that was retried is not read as a one-off.
        tried = f"(failed {failure.attempts} times)" if failure.attempts > 1 else ""
        lines.append(" ".join(part for part in ("fail", failure.test, where, failure.message, tried) if part))
    hidden = summary.failed_count - len(summary.failures)
    if hidden > 0:
        lines.append(f"… {hidden} more failures not listed")
    # A flaky test is counted among the passed and named nowhere else, so if it is not said here it is not said.
    for flaky in summary.flaky:
        lines.append(f"flaky {flaky.test} failed, then passed on attempt {flaky.attempts}")
    return lines
