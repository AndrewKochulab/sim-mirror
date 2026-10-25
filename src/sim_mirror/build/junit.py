# SPDX-License-Identifier: Apache-2.0
"""What an Android project's instrumented tests said, read from the JUnit XML Gradle writes, as `xcresult` says it.

``connected<Variant>AndroidTest`` writes one ``TEST-<device>-<module>.xml`` per device into
``build/outputs/androidTest-results/connected/<variant>/`` (measured with AGP 9.1): a ``testsuite`` per test class, a
``testcase`` per test, and a failure's text -- its message, then the stack the device saw:

    <testcase name="failsOnPurpose" classname="dev.simmirror.example.MainActivityTest" time="0.998">
      <failure>androidx.test.espresso...AssertionFailedWithCauseError: '... is "Bread"' doesn't match the selected view.
    ...
    at dev.simmirror.example.MainActivityTest.failsOnPurpose(MainActivityTest.kt:36)

A test is named as ``only_testing`` takes it back, ``package.Class/method``; a failure is placed at the first frame in
its own test class, else at the first frame in a file the project has. Each run of the tests -- the first, and each
retry of the ones that failed -- is read on its own, and `summary` tells a test that failed every time from one that
passed only on a later go (`xcresult.Flaky`), so the answer reads exactly as an Xcode test run's does.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ElementTree
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from sim_mirror.build.xcresult import LISTED_MAX, MESSAGE_MAX, Failure, Flaky, SuiteSummary

#: The largest result file read: a run's results are kilobytes; a file far larger is not one.
FILE_MAX = 8 * 1024 * 1024
#: A stack frame: ``at package.Class.method(File.kt:36)``.
_FRAME = re.compile(r"^\s*at ([\w$.]+)\.[\w$<>-]+\(([\w$.-]+\.(?:kt|java)):(\d+)\)", re.M)
#: A document that declares entities is not a result file Gradle wrote, and is not expanded.
_DECLARES = re.compile(rb"<!(?:DOCTYPE|ENTITY)", re.I)


@dataclass(frozen=True)
class Outcome:
    """One test, run once."""

    test: str
    state: str
    message: str = ""
    file: str | None = None
    line: int | None = None


def _short(text: str) -> str:
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return first if len(first) <= MESSAGE_MAX else first[: MESSAGE_MAX - 1] + "…"


def _where(text: str, test_class: str) -> tuple[str | None, int | None]:
    """The file and line a failure happened at: the first frame in the test's own class, else the first frame."""
    frames = [(owner, file, int(line)) for owner, file, line in _FRAME.findall(text)]
    own = [frame for frame in frames if frame[0] == test_class or frame[0].startswith(f"{test_class}$")]
    chosen = own or frames
    return (chosen[0][1], chosen[0][2]) if chosen else (None, None)


def _outcome(case: ElementTree.Element) -> Outcome:
    test_class, name = case.get("classname", ""), case.get("name", "")
    test = f"{test_class}/{name}" if test_class else name
    problem = case.find("failure")
    if problem is None:
        problem = case.find("error")
    if problem is not None:
        text = problem.text or problem.get("message") or ""
        file, line = _where(text, test_class)
        return Outcome(test, "failed", _short(problem.get("message") or text), file, line)
    return Outcome(test, "skipped" if case.find("skipped") is not None else "passed")


def read_results(paths: Iterable[Path]) -> list[Outcome]:
    """Every test the result files say was run, in the order they say it. A file that cannot be read, or is not a
    result file, says nothing."""
    outcomes: list[Outcome] = []
    for path in paths:
        try:
            raw = path.read_bytes() if path.stat().st_size <= FILE_MAX else b""
            root = ElementTree.fromstring(raw) if raw and not _DECLARES.search(raw) else None
        except (OSError, ElementTree.ParseError):
            root = None
        if root is not None:
            outcomes += [_outcome(case) for case in root.iter("testcase")]
    return outcomes


def failed(outcomes: Iterable[Outcome]) -> list[str]:
    """The tests that failed, as a retry names them."""
    return list(dict.fromkeys(outcome.test for outcome in outcomes if outcome.state == "failed"))


def summary(attempts: Sequence[Sequence[Outcome]], seconds: float | None) -> SuiteSummary:
    """What the runs of a test run said, together: the first ran every test, each later one the tests still failing.

    A test is what its last run said. One that failed every time it was run is a failure, with how many times; one
    that failed and then passed is flaky, and counted among the passed, as Xcode counts it.
    """
    runs: dict[str, list[Outcome]] = {}
    for attempt in attempts:
        for outcome in attempt:
            runs.setdefault(outcome.test, []).append(outcome)
    finals = {test: tried[-1] for test, tried in runs.items()}
    failures = [
        Failure(test, last.message, last.file, last.line, attempts=len(runs[test]))
        for test, last in finals.items()
        if last.state == "failed"
    ]
    flaky = tuple(
        Flaky(test, attempts=len(tried))
        for test, tried in runs.items()
        if tried[-1].state == "passed" and any(outcome.state == "failed" for outcome in tried)
    )
    passed = sum(1 for last in finals.values() if last.state == "passed")
    skipped = sum(1 for last in finals.values() if last.state == "skipped")
    return SuiteSummary(
        passed=not failures,
        passed_count=passed,
        failed_count=len(failures),
        skipped_count=skipped,
        failures=tuple(failures[:LISTED_MAX]),
        seconds=seconds,
        flaky=flaky,
    )
