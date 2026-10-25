# SPDX-License-Identifier: Apache-2.0
"""What a Gradle build that failed said, read from its log, as `xcresult.BuildSummary` says it.

Gradle writes no result file a failed compile can be read from, so its log is read, for the forms its compilers print
(measured with Gradle 9.3 and AGP 9.1; fixtures in `sim_mirror/testing/fixtures/gradle-*.log`):

    e: file:///…/MainActivity.kt:21:26 Initializer type mismatch: expected 'Int', actual 'String'.     (Kotlin)
    /…/Main.java:12: error: cannot find symbol                                                         (javac)
    dev.simmirror.example.app-main-5:/layout/main.xml:26: error: resource string/missing … not found.  (resources)

Kotlin's ``w:`` and javac's ``warning:`` lines are its warnings. A failure none of those explain -- a build script that
does not compile, a dependency that cannot be fetched -- is said by Gradle's own ``* What went wrong:`` section, which
is the one error then.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

from sim_mirror.build.project_files import ending_paths
from sim_mirror.build.xcresult import LISTED_MAX, MESSAGE_MAX, BuildSummary, Issue

_KOTLIN = re.compile(r"^([ew]): (?:file://)?(\S+?):(\d+):(\d+) (.+)$", re.M)
#: javac's, and the resource linker's where it gives a whole path.
_LOCATED = re.compile(r"^\s*(?:ERROR:\s*)?(/[^\s:]+\.\w+):(\d+): (?:AAPT: )?(error|warning): (.+)$", re.M)
#: The resource linker's, naming a resource by its folder and file after the module's own name.
_RESOURCE = re.compile(r"^\s*[\w.-]+:/([^\s:]+\.\w+):(\d+): (error|warning): (.+)$", re.M)
_WRONG = re.compile(r"^\* What went wrong:\n(.*?)(?=^\* (?:Try|Get more help|Exception is):|\Z)", re.M | re.S)
_KINDS = {"e": "error", "w": "warning"}


def _short(text: str) -> str:
    value = " ".join(text.split())
    return value if len(value) <= MESSAGE_MAX else value[: MESSAGE_MAX - 1] + "…"


def _what_went_wrong(log: str) -> list[str]:
    """Gradle's own account of why a build failed: each ``> `` line of its section, the task first."""
    found = _WRONG.findall(log)
    if not found:
        return []
    lines = [line.strip().removeprefix(">").strip() for line in found[-1].splitlines()]
    return [line for line in lines if line]


def read_log(log: str, folder: Path, *, succeeded: bool, seconds: float | None) -> BuildSummary:
    """The errors and warnings a build's log holds, each where it is in the project."""
    issues: list[Issue] = []
    for kind, file, line, column, message in _KOTLIN.findall(log):
        issues.append(Issue(_KINDS[kind], _short(message), file, int(line), int(column)))
    for file, line, kind, message in _LOCATED.findall(log):
        issues.append(Issue(kind, _short(message), file, int(line)))
    resources = [Issue(kind, _short(message), file, int(line)) for file, line, kind, message in _RESOURCE.findall(log)]
    # A resource is named from its module's resource folder -- ``layout/main.xml`` -- and found in the project.
    places = ending_paths(folder, [issue.file for issue in resources if issue.file])
    issues += [replace(issue, file=places.get(issue.file or "", issue.file)) for issue in resources]
    # Gradle prints a compiler's errors again in its failure summary: each is said once.
    issues = list(dict.fromkeys(issues))
    errors = [issue for issue in issues if issue.kind == "error"]
    warnings = [issue for issue in issues if issue.kind == "warning"]
    if not succeeded and not errors:
        wrong = _what_went_wrong(log)
        errors = [Issue("error", _short(" ".join(wrong) if wrong else "Gradle failed; the log says why"))]
    return BuildSummary(
        succeeded=succeeded,
        errors=tuple(errors[:LISTED_MAX]),
        warnings=tuple(warnings[:LISTED_MAX]),
        error_count=len(errors),
        warning_count=len(warnings),
        seconds=seconds,
    )
