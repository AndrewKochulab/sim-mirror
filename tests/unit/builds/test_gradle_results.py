# SPDX-License-Identifier: Apache-2.0
"""What a Gradle build or an Android test run said -- its log, its JUnit XML -- read as an Xcode run's answer is."""

from __future__ import annotations

from pathlib import Path

from sim_mirror.build import gradle_log, junit
from sim_mirror.build.project_files import ending_paths
from sim_mirror.build.xcresult import Flaky, render_build, render_tests
from sim_mirror.testing.fakes import FIXTURES, fixture

ROOT = Path("/Users/dev/Groceries")
TEST = "dev.simmirror.example.MainActivityTest"


def _results(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def _suite(*cases: str) -> str:
    return f'<?xml version="1.0"?><testsuites><testsuite name="{TEST}">{"".join(cases)}</testsuite></testsuites>'


def test_a_kotlin_compile_error_is_placed_in_the_project() -> None:
    summary = gradle_log.read_log(fixture("gradle-kotlin-error.log"), ROOT, succeeded=False, seconds=4.2)
    assert render_build(summary, label="app (debug)", root=ROOT) == [
        "build FAILED · app (debug) · 4.2s · 1 error, 0 warnings",
        "error app/src/main/kotlin/dev/simmirror/example/MainActivity.kt:21:26 Initializer type mismatch: expected "
        "'Int', actual 'String'.",
    ]


def test_a_resource_error_is_found_from_its_folder_and_file(tmp_path: Path) -> None:
    layout = tmp_path / "app/src/main/res/layout/main.xml"
    layout.parent.mkdir(parents=True)
    layout.touch()
    summary = gradle_log.read_log(fixture("gradle-aapt-error.log"), tmp_path, succeeded=False, seconds=None)
    assert [(issue.file, issue.line) for issue in summary.errors] == [("app/src/main/res/layout/main.xml", 26)]
    assert summary.errors[0].message.startswith("resource string/missing")


def test_javac_and_kotlin_warnings_are_warnings_and_each_error_is_said_once() -> None:
    log = "\n".join(
        [
            "w: file:///Users/dev/Groceries/app/src/main/kotlin/Main.kt:3:5 'x' is never used",
            "/Users/dev/Groceries/app/src/main/java/Old.java:12: error: cannot find symbol",
            "/Users/dev/Groceries/app/src/main/java/Old.java:12: error: cannot find symbol",
            "ERROR: /Users/dev/Groceries/app/src/main/res/values/strings.xml:4: AAPT: error: bad name",
            "/Users/dev/Groceries/app/src/main/java/Old.java:20: warning: [deprecation] old",
        ]
    )
    summary = gradle_log.read_log(log, ROOT, succeeded=False, seconds=None)
    assert render_build(summary, label="app (debug)", root=ROOT, warnings=True) == [
        "build FAILED · app (debug) · 2 errors, 2 warnings",
        "error app/src/main/java/Old.java:12 cannot find symbol",
        "error app/src/main/res/values/strings.xml:4 bad name",
        "warning app/src/main/kotlin/Main.kt:3:5 'x' is never used",
        "warning app/src/main/java/Old.java:20 [deprecation] old",
    ]


def test_a_failure_no_compiler_explains_is_what_gradle_says_went_wrong() -> None:
    log = (
        "* What went wrong:\nTask 'assembleFree' not found in project ':app'.\n\n"
        "* Try:\n> Run gradle tasks to get a list of available tasks.\n"
    )
    summary = gradle_log.read_log(log, ROOT, succeeded=False, seconds=None)
    assert [issue.message for issue in summary.errors] == ["Task 'assembleFree' not found in project ':app'."]
    summary = gradle_log.read_log("killed\n", ROOT, succeeded=False, seconds=None)
    assert [issue.message for issue in summary.errors] == ["Gradle failed; the log says why"]
    assert gradle_log.read_log("BUILD SUCCESSFUL\n", ROOT, succeeded=True, seconds=1.0).errors == ()
    long = gradle_log.read_log("* What went wrong:\n" + "x" * 400 + "\n", ROOT, succeeded=False, seconds=None)
    assert len(long.errors[0].message) == 300 and long.errors[0].message.endswith("…")


def test_a_failed_instrumented_test_is_placed_at_its_own_frame() -> None:
    outcomes = junit.read_results([FIXTURES / "junit-android-failure.xml"])
    assert [(outcome.test, outcome.state) for outcome in outcomes] == [
        (f"{TEST}/startsEmpty", "passed"),
        (f"{TEST}/failsOnPurpose", "failed"),
    ]
    failed = outcomes[1]
    assert (failed.file, failed.line) == ("MainActivityTest.kt", 36)
    assert failed.message.endswith("""is "Bread"' doesn't match the selected view.""")
    lines = render_tests(junit.summary([outcomes], 2.2), label="app (debug)")
    assert lines[0] == "test FAILED · app (debug) · 1 passed, 1 failed, 0 skipped · 2.2s"
    assert lines[1].startswith(f"fail {TEST}/failsOnPurpose MainActivityTest.kt:36 androidx.test.espresso")


def test_a_test_that_failed_every_go_is_a_failure_and_one_that_passed_later_is_flaky(tmp_path: Path) -> None:
    error = '<error message="boom">java.lang.IllegalStateException: boom\n\tat other.Helper.run(Helper.java:9)</error>'
    first = _results(
        tmp_path,
        "first.xml",
        _suite(
            f'<testcase name="flaky" classname="{TEST}"><failure>once\n\tat {TEST}$Inner.run(MainActivityTest.kt:50)'
            "</failure></testcase>",
            f'<testcase name="broken" classname="{TEST}">{error}</testcase>',
            f'<testcase name="later" classname="{TEST}"><skipped/></testcase>',
            '<testcase name="bare"><failure/></testcase>',
        ),
    )
    second = _results(
        tmp_path,
        "second.xml",
        _suite(
            f'<testcase name="flaky" classname="{TEST}"/>',
            f'<testcase name="broken" classname="{TEST}">{error}</testcase>',
            '<testcase name="bare"><failure message="still"/></testcase>',
        ),
    )
    attempts = [junit.read_results([first]), junit.read_results([second])]
    assert junit.failed(attempts[0]) == [f"{TEST}/flaky", f"{TEST}/broken", "bare"]
    assert attempts[0][0].file == "MainActivityTest.kt" and attempts[0][0].line == 50
    summary = junit.summary(attempts, None)
    assert (summary.passed, summary.passed_count, summary.failed_count, summary.skipped_count) == (False, 1, 2, 1)
    assert [(failure.test, failure.message, failure.file, failure.attempts) for failure in summary.failures] == [
        (f"{TEST}/broken", "boom", "Helper.java", 2),
        ("bare", "still", None, 2),
    ]
    assert summary.flaky == (Flaky(f"{TEST}/flaky", 2),)


def test_a_file_that_is_not_a_result_says_nothing(tmp_path: Path) -> None:
    entities = _results(tmp_path, "entities.xml", '<!DOCTYPE x [<!ENTITY a "a">]><testsuites/>')
    broken = _results(tmp_path, "broken.xml", "<testsuites>")
    assert junit.read_results([entities, broken, tmp_path / "gone.xml"]) == []
    assert junit.summary([], None).passed


def test_a_failures_message_is_its_first_line_cut_to_length(tmp_path: Path) -> None:
    results = _results(
        tmp_path,
        "long.xml",
        _suite(f'<testcase name="t" classname="{TEST}"><failure>\n{"y" * 400}</failure></testcase>'),
    )
    (outcome,) = junit.read_results([results])
    assert len(outcome.message) == 300 and outcome.message.endswith("…") and outcome.file is None


def test_a_path_end_is_the_one_project_file_it_ends(tmp_path: Path) -> None:
    for path in ("app/src/main/res/layout/main.xml", "lib/src/main/res/values/strings.xml", "a/Same.kt", "b/Same.kt"):
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).touch()
    names = ["layout/main.xml", "values/strings.xml", "Same.kt", "/abs/x.xml", "", "Gone.kt"]
    assert ending_paths(tmp_path, names) == {
        "layout/main.xml": "app/src/main/res/layout/main.xml",
        "values/strings.xml": "lib/src/main/res/values/strings.xml",
    }
