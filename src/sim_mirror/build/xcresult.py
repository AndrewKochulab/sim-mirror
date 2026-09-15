# SPDX-License-Identifier: Apache-2.0
"""What a build or a test run said, read from its result bundle, as a few lines an agent can act on.

The result bundle tool's ``get build-results`` and ``get test-results summary|tests`` answer in JSON (measured on Xcode
26.6; fixtures in `sim_mirror/testing/fixtures/`). This turns that into what an agent needs next, and nothing it would
have to wade through:

    build FAILED · NotesProbe (Debug) · 1.5s · 1 error, 0 warnings
    error Sources/NotesApp.swift:39:19 Cannot convert value of type 'String' to specified type 'Int'

    test FAILED · NotesProbe · 2 passed, 1 failed, 0 skipped · 34.3s
    fail NotesTests/testDeliberatelyFails() NotesTests.swift:10 XCTAssertEqual failed: ("Trip") is not equal to …

JSON in, text out. A result bundle's source locations count lines and columns from zero; shown here as an editor shows
them, from one. A path inside the project folder is shown relative to it -- compared with symlinks resolved, since a
result bundle names `/tmp/…` where the folder is `/private/tmp/…`.

A compile that fails also fails the steps copying what it would have produced, each an error with no location ('The
file "App.swiftmodule" could not be opened because there is no such file', in Xcode's curly quotes). Beside a located
error those say nothing an agent can act on, so they are counted in one line rather than listed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

#: The most errors, warnings or failures listed; the counts still say how many there were.
LISTED_MAX = 20
MESSAGE_MAX = 300

_FAILURE_AT = re.compile(r"\A(?P<file>[^:\s]+\.[A-Za-z]+):(?P<line>\d+): ")
#: The error a copy step gives for a file a failed compile never produced.
_NOT_PRODUCED = re.compile(r"couldn.t be opened because there is no such file")


def _resolved(path: Path) -> Path:
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        return path


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
        path = Path(self.file)
        if root is not None:
            resolved, base = _resolved(path), _resolved(root)
            if resolved.is_relative_to(base):
                path = resolved.relative_to(base)
        return f"{path}" + (f":{self.line}" if self.line else "") + (f":{self.column}" if self.column else "")


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


@dataclass(frozen=True)
class SuiteSummary:
    passed: bool
    passed_count: int
    failed_count: int
    skipped_count: int
    failures: tuple[Failure, ...]
    seconds: float | None


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


def _failure_places(tests: object) -> dict[str, tuple[str, int]]:
    """Each failed test's first failure location, from the tree of tests: identifier -> (file, line)."""
    places: dict[str, tuple[str, int]] = {}
    stack = list(tests.get("testNodes") or []) if isinstance(tests, dict) else []
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        children = node.get("children") or []
        if node.get("nodeType") == "Test Case" and node.get("nodeIdentifier") not in places:
            for child in children:
                match = _FAILURE_AT.match(str(child.get("name") or "")) if isinstance(child, dict) else None
                if match and child.get("nodeType") == "Failure Message":
                    places[node["nodeIdentifier"]] = (match.group("file"), int(match.group("line")))
                    break
        stack.extend(children)
    return places


def suite_summary(summary: dict[str, Any], tests: dict[str, Any] | None = None) -> SuiteSummary:
    places = _failure_places(tests)
    failures = []
    listed = summary.get("testFailures")
    for entry in (listed if isinstance(listed, list) else [])[:LISTED_MAX]:
        if isinstance(entry, dict):
            name = str(entry.get("testIdentifierString") or entry.get("testName") or "a test")
            message = _FAILURE_AT.sub("", str(entry.get("failureText") or ""))
            file, line = places.get(name, (None, None))
            failures.append(Failure(name, _short(message), file, line))
    return SuiteSummary(
        passed=summary.get("result") == "Passed",
        passed_count=_count(summary.get("passedTests"), 0),
        failed_count=_count(summary.get("failedTests"), 0),
        skipped_count=_count(summary.get("skippedTests"), 0),
        failures=tuple(failures),
        seconds=_seconds(summary.get("startTime"), summary.get("finishTime")),
    )


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _took(seconds: float | None) -> str:
    return f" · {seconds:g}s" if seconds is not None else ""


def render_build(summary: BuildSummary, *, label: str, root: Path | None, warnings: bool = False) -> list[str]:
    """The lines a build answers with: the verdict, then its errors -- and its warnings, when asked for."""
    verdict = "ok" if summary.succeeded else "FAILED"
    lines = [
        f"build {verdict} · {label}{_took(summary.seconds)} · {_plural(summary.error_count, 'error')}, "
        f"{_plural(summary.warning_count, 'warning')}"
    ]
    located = any(error.file for error in summary.errors)
    follow_ons = [
        error for error in summary.errors if located and not error.file and _NOT_PRODUCED.search(error.message)
    ]
    errors = [error for error in summary.errors if error not in follow_ons]
    shown = [*errors, *(summary.warnings if warnings else ())]
    lines += [" ".join(part for part in (issue.kind, issue.where(root), issue.message) if part) for issue in shown]
    if follow_ons:
        lines.append(f"… {_plural(len(follow_ons), 'follow-on error')} from that failure: files it did not produce")
    listed = len(summary.errors) + (len(summary.warnings) if warnings else 0)
    hidden = summary.error_count + (summary.warning_count if warnings else 0) - listed
    if hidden > 0:
        lines.append(f"… {hidden} more not listed")
    return lines


def render_tests(summary: SuiteSummary, *, label: str) -> list[str]:
    """The lines a test run answers with: the verdict and counts, then each failure where it happened."""
    verdict = "ok" if summary.passed else "FAILED"
    lines = [
        f"test {verdict} · {label} · {summary.passed_count} passed, {summary.failed_count} failed, "
        f"{summary.skipped_count} skipped{_took(summary.seconds)}"
    ]
    for failure in summary.failures:
        where = f"{failure.file}:{failure.line}" if failure.file else ""
        lines.append(" ".join(part for part in ("fail", failure.test, where, failure.message) if part))
    hidden = summary.failed_count - len(summary.failures)
    if hidden > 0:
        lines.append(f"… {hidden} more failures not listed")
    return lines
