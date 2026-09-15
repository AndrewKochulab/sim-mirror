# SPDX-License-Identifier: Apache-2.0
"""run_xcrun: an argv, the chosen Xcode, a timeout that reaps, and a missing Xcode as a result."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from sim_mirror.platform import xcrun
from sim_mirror.platform.xcrun import (
    CANNOT_RUN,
    TIMED_OUT,
    XCRUN_MISSING,
    XcrunResult,
    run_xcrun,
    xcrun_binary,
    xcrun_env,
)


class _Proc:
    def __init__(self, rc: int | None = 0, out: bytes | None = b"", err: bytes | None = b"", hang: bool = False):
        self.returncode = rc
        self._out, self._err, self._hang = out, err, hang
        self.killed = self.waited = False
        self.data: bytes | None = None

    async def communicate(self, data: bytes | None = None) -> tuple[bytes | None, bytes | None]:
        self.data = data
        if self._hang:
            await asyncio.sleep(10)
        return self._out, self._err

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> None:
        self.waited = True


Install = Callable[..., dict[str, Any]]


@pytest.fixture
def spawn(monkeypatch: pytest.MonkeyPatch) -> Install:
    seen: dict[str, Any] = {}

    def install(proc: _Proc | None = None, error: Exception | None = None) -> dict[str, Any]:
        async def fake_exec(*args: str, **kwargs: Any) -> _Proc | None:
            seen["argv"] = args
            seen.update(kwargs)
            if error is not None:
                raise error
            return proc

        monkeypatch.setattr(xcrun.asyncio, "create_subprocess_exec", fake_exec)
        monkeypatch.setattr(xcrun, "xcrun_binary", lambda: "/usr/bin/xcrun")
        return seen

    return install


async def test_a_call_runs_xcrun_with_its_argv_on_the_chosen_xcode(
    spawn: Install, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DEVELOPER_DIR", raising=False)
    seen = spawn(_Proc(out=b"ok\n", err=b""))
    result = await run_xcrun(
        "simctl", "list", "devices", "-j", developer_dir="/Applications/Xcode-26.app/Contents/Developer"
    )
    assert result == XcrunResult(0, "ok\n", "") and result.ok
    assert seen["argv"] == ("/usr/bin/xcrun", "simctl", "list", "devices", "-j")
    assert seen["env"]["DEVELOPER_DIR"] == "/Applications/Xcode-26.app/Contents/Developer"
    assert seen["stdin"] == asyncio.subprocess.DEVNULL and seen["cwd"] is None


async def test_input_goes_to_a_pipe_and_the_directory_is_kept(spawn: Install, tmp_path: Path) -> None:
    proc = _Proc(rc=None, out=b"caf\xe9", err=None)
    seen = spawn(proc)
    result = await run_xcrun("simctl", "pbcopy", "x", input_data=b"hello", cwd=tmp_path)
    assert seen["stdin"] == asyncio.subprocess.PIPE and seen["cwd"] == str(tmp_path)
    assert proc.data == b"hello"
    assert result.rc == 0 and result.raw == b"caf\xe9" and result.out == "caf�" and result.err == ""


def test_the_environment_names_an_xcode_only_when_one_is_chosen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEVELOPER_DIR", raising=False)
    assert "DEVELOPER_DIR" not in xcrun_env("")
    assert xcrun_env("/X")["DEVELOPER_DIR"] == "/X"


async def test_no_xcode_is_a_result_not_an_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(xcrun, "xcrun_binary", lambda: None)
    result = await run_xcrun("simctl", "list")
    assert result.rc == XCRUN_MISSING and "no xcrun" in result.err


async def test_an_xcrun_that_cannot_start_is_a_result(spawn: Install) -> None:
    spawn(error=OSError("bad cpu type"))
    assert await run_xcrun("simctl", "list") == XcrunResult(CANNOT_RUN, "", "cannot run xcrun: bad cpu type")


async def test_a_hung_call_is_killed_reaped_and_reported(spawn: Install) -> None:
    proc = _Proc(hang=True)
    spawn(proc)
    result = await run_xcrun("simctl", "bootstatus", "u", "-b", timeout=0.01)
    assert result.rc == TIMED_OUT and "simctl bootstatus did not finish within 0.01 seconds" in result.err
    assert proc.killed and proc.waited


def test_the_system_xcrun_is_preferred_then_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    system = tmp_path / "xcrun"
    system.write_text("#!/bin/sh\n")
    system.chmod(0o755)
    monkeypatch.setattr(xcrun, "SYSTEM_XCRUN", str(system))
    assert xcrun_binary() == str(system)
    monkeypatch.setattr(xcrun, "SYSTEM_XCRUN", str(tmp_path / "missing"))
    monkeypatch.setattr(xcrun.shutil, "which", lambda name: f"/elsewhere/{name}")
    assert xcrun_binary() == "/elsewhere/xcrun"


def test_a_results_message_is_its_last_useful_line() -> None:
    assert XcrunResult(1, "out\n", "first\n\nsecond  \n").message == "second"
    assert XcrunResult(1, "a\nlast\n", "  \n").message == "last"
    assert XcrunResult(3, "", "").message == "xcrun exited with 3"


async def test_a_long_call_starts_in_its_own_group_on_the_chosen_xcode_with_its_output_logged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: dict[str, Any] = {}

    async def fake_spawn(argv: list[str], log_path: Path, *, cwd: Path | None = None, env: Any = None) -> str:
        seen.update(argv=argv, log_path=log_path, cwd=cwd, env=env)
        return "process"

    monkeypatch.setattr(xcrun, "spawn", fake_spawn)
    monkeypatch.setattr(xcrun, "xcrun_binary", lambda: "/usr/bin/xcrun")
    log = tmp_path / "b1.log"
    assert (
        await xcrun.start_xcrun("xcodebuild", "build", log_path=log, developer_dir="/X.app", cwd=tmp_path) == "process"
    )
    assert seen["argv"] == ["/usr/bin/xcrun", "xcodebuild", "build"] and seen["log_path"] == log
    assert seen["cwd"] == tmp_path and seen["env"]["DEVELOPER_DIR"] == "/X.app"


async def test_a_long_call_without_xcode_fails_as_a_missing_program_would(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(xcrun, "xcrun_binary", lambda: None)
    with pytest.raises(FileNotFoundError, match="no xcrun"):
        await xcrun.start_xcrun("xcodebuild", log_path=tmp_path / "b1.log")
