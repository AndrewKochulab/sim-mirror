# SPDX-License-Identifier: Apache-2.0
"""The guards: forbidden programs refused however they are started, others let through, and state moved aside."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sim_mirror.testing import guards
from sim_mirror.testing.guards import RefusedSubprocess, forbidden


@pytest.mark.parametrize(
    ("argv", "program"),
    [
        (["xcrun", "simctl", "list"], "xcrun"),
        (["/opt/homebrew/bin/idb_companion", "--udid", "U"], "idb_companion"),
        (["env", "DEVELOPER_DIR=/X.app", "xcodebuild", "-list"], "xcodebuild"),
        (["tmux", "new-session", "--", "claude", "--model", "x"], "claude"),
        # A shell command line's first word counts: `sh -c "osascript …"` runs osascript.
        (["/bin/sh", "-c", "osascript -e 'beep'"], "osascript"),
        ([["open", "-a", "Simulator"]], "open"),
    ],
)
def test_the_program_an_argv_would_run_is_found_wherever_it_is(argv: list[object], program: str) -> None:
    assert forbidden(argv) == program


def test_a_caller_can_forbid_other_programs() -> None:
    assert forbidden(["sh", "-c", "true"], frozenset({"sh"})) == "sh"
    assert forbidden(["sh", "-c", "true"]) is None


def test_arguments_that_merely_mention_nothing_forbidden_pass() -> None:
    assert forbidden(["git", "log", "--format=%H", "opener"]) is None
    assert forbidden([]) is None
    assert forbidden([["ls", "-l"], "grep"]) is None


@pytest.fixture
def guarded(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    guards.install_subprocess_guard(monkeypatch)
    return monkeypatch


def test_run_and_popen_refuse_a_forbidden_program_before_starting_it(guarded: pytest.MonkeyPatch) -> None:
    with pytest.raises(RefusedSubprocess, match="tried to run 'xcrun'"):
        subprocess.run(["xcrun", "simctl", "list"], check=False)
    with pytest.raises(RefusedSubprocess, match="'claude'"):
        subprocess.Popen(args=["env", "claude"])
    with pytest.raises(RefusedSubprocess, match="'idb_companion'"):
        subprocess.run("idb_companion --list", shell=True, check=False)
    with pytest.raises(RefusedSubprocess, match="'open'"):
        subprocess.run(b"open -a Simulator", shell=True, check=False)


async def test_async_starts_refuse_a_forbidden_program_before_starting_it(guarded: pytest.MonkeyPatch) -> None:
    with pytest.raises(RefusedSubprocess, match="create_subprocess_exec tried to run 'xcodebuild'"):
        await asyncio.create_subprocess_exec("/usr/bin/xcodebuild", "-version")
    with pytest.raises(RefusedSubprocess, match="create_subprocess_shell tried to run 'osascript'"):
        await asyncio.create_subprocess_shell("osascript -e 'beep'")


async def test_other_programs_still_run(guarded: pytest.MonkeyPatch) -> None:
    done = subprocess.run([sys.executable, "-c", "print('hi')"], capture_output=True, text=True, check=True)
    assert done.stdout == "hi\n"
    proc = await asyncio.create_subprocess_exec(sys.executable, "-c", "print(1)", stdout=asyncio.subprocess.PIPE)
    out, _ = await proc.communicate()
    assert out == b"1\n"
    shell = await asyncio.create_subprocess_shell("true")
    assert await shell.wait() == 0


def test_the_suite_itself_runs_under_the_guard() -> None:
    with pytest.raises(RefusedSubprocess):
        subprocess.run(["xcrun", "--version"], check=False)


def test_state_folders_and_configuration_are_moved_under_a_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    moved = guards.isolate_state(monkeypatch, tmp_path)
    assert moved["SIM_MIRROR_RUN_DIR"] == tmp_path / "run"
    assert moved["SIM_MIRROR_CONFIG"] == tmp_path / "config.toml"
    assert {os.environ[name] for name in guards.STATE_FOLDERS} == {
        str(tmp_path / folder) for folder in guards.STATE_FOLDERS.values()
    }


def test_the_suite_itself_runs_with_its_state_moved(tmp_path: Path) -> None:
    assert os.environ["SIM_MIRROR_CLAIMS_DIR"] == str(tmp_path / "sim-mirror" / "claims")
