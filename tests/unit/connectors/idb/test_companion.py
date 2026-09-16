# SPDX-License-Identifier: Apache-2.0
"""A companion's life: found, started in its own group on a short socket, ready when it answers, ended, and orphans
found -- only this host's."""

from __future__ import annotations

import asyncio
import os
import signal
import stat
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from sim_mirror.connectors.base import ConnectorError, Screen
from sim_mirror.connectors.idb import companion as companion_module
from sim_mirror.connectors.idb.companion import (
    READY_POLL_S,
    STOP_TIMEOUT_S,
    CompanionLauncher,
    CompanionUnavailable,
    Recorded,
    companion_argv,
    companion_id,
    companion_version,
    find_companion,
    read_pid_file,
    recorded_companions,
)
from sim_mirror.host_copy import HostCopy
from sim_mirror.platform.developer_dir import DEVELOPER_DIR

UDID = "D946616B-6E4F-4F5C-8C76-54FAD9B7D702"
BINARY = "/opt/homebrew/bin/idb_companion"
TAG = "SimMirror"
#: The process the rig's launcher speaks for, and another process of the same host running beside it.
OWNER = 777
OTHER = 888
XCODE_26 = "/Applications/Xcode.app/Contents/Developer"
XCODE_27 = "/Applications/Xcode 27 beta.app/Contents/Developer"


class FakeProcess:
    def __init__(self, pid: int = 4242, returncode: int | None = None) -> None:
        self.pid = pid
        self.returncode = returncode
        self.waited = False

    async def wait(self) -> int | None:
        self.waited = True
        return self.returncode


class FakeEngine:
    def __init__(self, failures: int = 0, close_error: Exception | None = None) -> None:
        self.failures = failures
        self.close_error = close_error
        self.described = 0
        self.closed = 0

    async def describe(self) -> Screen:
        self.described += 1
        if self.failures:
            self.failures -= 1
            raise ConnectorError("not yet")
        return Screen(1206, 2622, 402, 874, 3.0)

    async def close(self) -> None:
        self.closed += 1
        if self.close_error:
            raise self.close_error


class Rig:
    def __init__(
        self,
        tmp_path: Path,
        *,
        engine: Any = None,
        socket_appears: bool = True,
        spawn_error: Exception | None = None,
        exit_code: int | None = None,
        stubborn: bool = False,
        alive: Sequence[int] = (),
        commands: dict[int, str] | None = None,
    ) -> None:
        self.now = [0.0]
        self.signals: list[tuple[int, int]] = []
        self.spawned: list[tuple[tuple[str, ...], Path]] = []
        self.envs: list[Mapping[str, str] | None] = []
        self.connected: list[str] = []
        self.process = FakeProcess(returncode=exit_code)
        self.engine = engine or FakeEngine()
        self.alive = set(alive)
        self.commands = commands or {}
        self.run = tmp_path / "run"

        async def spawn(argv: Sequence[str], log: Path, /, *, env: Mapping[str, str] | None = None) -> FakeProcess:
            self.spawned.append((tuple(argv), log))
            self.envs.append(env)
            if spawn_error:
                raise spawn_error
            if socket_appears:
                (self.run / f"{companion_id(UDID)}.sock").touch()
            return self.process

        def connect(path: str) -> Any:
            self.connected.append(path)
            return self.engine

        def signal_group(pid: int, sig: int) -> None:
            self.signals.append((pid, sig))
            if not stubborn or sig == signal.SIGKILL:
                if pid == self.process.pid:
                    self.process.returncode = -sig
                self.alive.discard(pid)

        async def command_of(pid: int) -> str | None:
            return self.commands.get(pid)

        async def sleep(seconds: float) -> None:
            self.now[0] += seconds

        self.launcher = CompanionLauncher(
            run_dir=self.run,
            log_dir=tmp_path / "logs",
            owner_tag=TAG,
            copy=HostCopy(owner_name="host"),
            spawn=spawn,
            connect=connect,
            signal_group=signal_group,
            pid_alive=lambda pid: pid in self.alive,
            command_of=command_of,
            clock=lambda: self.now[0],
            sleep=sleep,
            owner=OWNER,
        )


async def test_a_companion_starts_in_its_group_on_a_short_private_socket_and_is_ready_when_it_answers(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path)
    started = await rig.launcher.start(BINARY, UDID)
    name = companion_id(UDID)
    assert started.socket == rig.run / f"{name}.sock" and len(name) == 12
    assert rig.spawned == [(companion_argv(BINARY, UDID, started.socket), tmp_path / "logs" / f"idb-{name}.log")]
    assert rig.spawned[0][0] == (
        BINARY,
        "--udid",
        UDID,
        "--grpc-domain-sock",
        str(started.socket),
        "--log-level",
        "info",
    )
    assert stat.S_IMODE(rig.run.stat().st_mode) == 0o700
    assert started.pid_file.read_text() == f"4242 {OWNER} {TAG}" and started.alive
    assert started.engine is rig.engine and rig.engine.described == 1 and rig.connected == [str(started.socket)]


async def test_a_companion_runs_with_the_xcode_it_is_given_whatever_this_process_inherited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DEVELOPER_DIR, XCODE_26)
    rig = Rig(tmp_path)
    started = await rig.launcher.start(BINARY, UDID, XCODE_27)
    env = rig.envs[0]
    assert env is not None and env[DEVELOPER_DIR] == XCODE_27 and started.developer_dir == XCODE_27
    assert started.pid_file.read_text() == f"4242 {OWNER} {TAG}\n{XCODE_27}"
    # A reader that splits the whole file, as one written before the Xcode line did, still finds pid, owner and tag.
    assert started.pid_file.read_text().split()[:3] == ["4242", str(OWNER), TAG]
    assert read_pid_file(started.pid_file) == Recorded(4242, OWNER, TAG, XCODE_27)


async def test_a_companion_given_no_xcode_inherits_this_processs_and_its_pid_file_names_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DEVELOPER_DIR, XCODE_26)
    rig = Rig(tmp_path)
    started = await rig.launcher.start(BINARY, UDID)
    env = rig.envs[0]
    assert env is not None and env[DEVELOPER_DIR] == XCODE_26 and started.developer_dir == ""
    assert read_pid_file(started.pid_file) == Recorded(4242, OWNER, TAG, None)


async def test_a_companion_that_is_slow_to_answer_is_asked_again(tmp_path: Path) -> None:
    rig = Rig(tmp_path, engine=FakeEngine(failures=2))
    await rig.launcher.start(BINARY, UDID)
    assert rig.engine.described == 3 and rig.engine.closed == 2
    assert rig.now[0] == pytest.approx(2 * READY_POLL_S)


@pytest.mark.parametrize(
    ("kwargs", "status", "message"),
    [
        ({"socket_appears": False}, 504, "did not answer within 15 seconds$"),
        ({"engine": FakeEngine(failures=10_000)}, 504, "did not answer within 15 seconds: not yet"),
        ({"exit_code": 1}, 502, r"exited as it started \(exit 1\)"),
    ],
)
async def test_a_companion_that_never_becomes_ready_is_ended_and_says_why(
    tmp_path: Path, kwargs: dict[str, Any], status: int, message: str
) -> None:
    rig = Rig(tmp_path, **kwargs)
    with pytest.raises(CompanionUnavailable, match=message) as caught:
        await rig.launcher.start(BINARY, UDID)
    assert caught.value.status == status
    assert (4242, signal.SIGTERM) in rig.signals
    assert list(rig.run.iterdir()) == []


class Hanging(FakeEngine):
    """A companion that has made its socket but will not answer while the test watches."""

    async def describe(self) -> Screen:
        self.described += 1
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


async def test_a_start_let_go_while_the_companion_starts_ends_it_and_leaves_nothing_behind(tmp_path: Path) -> None:
    rig = Rig(tmp_path, engine=Hanging(close_error=RuntimeError("already closed")))
    starting = asyncio.create_task(rig.launcher.start(BINARY, UDID))
    while rig.engine.described == 0:
        await asyncio.sleep(0)
    starting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await starting
    assert (4242, signal.SIGTERM) in rig.signals and rig.engine.closed == 1
    assert list(rig.run.iterdir()) == []


async def test_a_companion_that_cannot_be_started_says_so(tmp_path: Path) -> None:
    rig = Rig(tmp_path, spawn_error=OSError("Bad CPU type in executable"))
    with pytest.raises(CompanionUnavailable, match="could not be started: Bad CPU type") as caught:
        await rig.launcher.start(BINARY, UDID)
    assert caught.value.status == 502


async def test_stopping_ends_the_group_and_tidies_up(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    started = await rig.launcher.start(BINARY, UDID)
    await rig.launcher.stop(started)
    assert rig.signals == [(4242, signal.SIGTERM)] and not started.alive
    assert rig.engine.closed == 1 and rig.process.waited
    assert not started.socket.exists() and not started.pid_file.exists()


async def test_a_companion_that_ignores_term_is_killed_and_a_failing_close_does_not_stop_that(tmp_path: Path) -> None:
    rig = Rig(tmp_path, stubborn=True, engine=FakeEngine(close_error=RuntimeError("channel gone")))
    started = await rig.launcher.start(BINARY, UDID)
    await rig.launcher.stop(started)
    assert rig.signals == [(4242, signal.SIGTERM), (4242, signal.SIGKILL)]
    assert rig.now[0] >= STOP_TIMEOUT_S


async def test_orphans_are_ended_only_while_their_pid_still_runs_a_companion(tmp_path: Path) -> None:
    rig = Rig(tmp_path, alive={5001, 5002}, commands={5001: f"{BINARY} --udid {UDID}", 5002: "/usr/bin/python3 x"})
    rig.run.mkdir()
    (rig.run / "a.pid").write_text("5001")
    (rig.run / "a.sock").touch()
    (rig.run / "b.pid").write_text("5002")
    (rig.run / "c.pid").write_text("garbage")
    (rig.run / "d.pid").write_text("5004")
    (rig.run / "e.pid").write_text("1")
    assert await rig.launcher.reap_orphans() == 1
    assert rig.signals == [(5001, signal.SIGTERM)]
    assert list(rig.run.iterdir()) == []


async def test_starting_ends_a_companion_left_running_for_the_same_device(tmp_path: Path) -> None:
    rig = Rig(tmp_path, alive={6001}, commands={6001: "idb_companion --udid x"})
    rig.run.mkdir()
    (rig.run / f"{companion_id(UDID)}.pid").write_text("6001")
    await rig.launcher.start(BINARY, UDID)
    assert rig.signals[0] == (6001, signal.SIGTERM)


async def test_a_companion_a_live_process_or_another_host_started_is_not_an_orphan(tmp_path: Path) -> None:
    companion = f"{BINARY} --udid {UDID}"
    rig = Rig(
        tmp_path, alive={5001, 5002, 5003, 5005, OTHER}, commands={pid: companion for pid in (5001, 5002, 5003, 5005)}
    )
    rig.run.mkdir()
    (rig.run / "theirs.pid").write_text(f"5001 {OTHER} {TAG}")
    (rig.run / "theirs.sock").touch()
    (rig.run / "gone-owner.pid").write_text(f"5002 999 {TAG}")
    (rig.run / "ours.pid").write_text(f"5003 {OWNER} {TAG}")
    (rig.run / "other-host.pid").write_text("5005 999 SomeHost")
    assert await rig.launcher.reap_orphans() == 2
    assert sorted(rig.signals) == [(5002, signal.SIGTERM), (5003, signal.SIGTERM)]
    assert sorted(path.name for path in rig.run.iterdir()) == ["other-host.pid", "theirs.pid", "theirs.sock"]


async def test_an_owner_whose_companion_has_already_gone_does_not_keep_its_files(tmp_path: Path) -> None:
    rig = Rig(tmp_path, alive={OTHER}, commands={})
    rig.run.mkdir()
    (rig.run / "stale.pid").write_text(f"5001 {OTHER} {TAG}")
    assert await rig.launcher.reap_orphans() == 0
    assert rig.signals == [] and list(rig.run.iterdir()) == []


@pytest.mark.parametrize(("tag", "named"), [(TAG, TAG), ("OtherHost", "OtherHost")])
async def test_starting_refuses_a_device_another_live_process_is_showing(tmp_path: Path, tag: str, named: str) -> None:
    rig = Rig(tmp_path, alive={6001, OTHER}, commands={6001: "idb_companion --udid x"})
    rig.run.mkdir()
    (rig.run / f"{companion_id(UDID)}.pid").write_text(f"6001 {OTHER} {tag}")
    with pytest.raises(CompanionUnavailable, match=rf"Another {named} on this Mac \(pid {OTHER}\)") as caught:
        await rig.launcher.start(BINARY, UDID)
    assert caught.value.status == 409 and "give this host a different device" in str(caught.value)
    assert rig.signals == [] and rig.spawned == []


async def test_this_process_under_another_tag_is_another_owner_and_an_untagged_file_is_this_hosts(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path, alive={6001, OWNER}, commands={6001: "idb_companion"})
    rig.run.mkdir()
    pid_file = rig.run / f"{companion_id(UDID)}.pid"
    pid_file.write_text(f"6001 {OWNER} OtherHost")
    with pytest.raises(CompanionUnavailable, match="Another OtherHost"):
        await rig.launcher.start(BINARY, UDID)
    pid_file.write_text(f"6001 {OTHER}")
    rig.alive.add(OTHER)
    with pytest.raises(CompanionUnavailable, match=f"Another {TAG}"):
        await rig.launcher.start(BINARY, UDID)


async def test_a_companion_this_live_process_left_for_the_device_is_ended_before_a_new_one(tmp_path: Path) -> None:
    rig = Rig(tmp_path, alive={6001, OWNER}, commands={6001: "idb_companion --udid x"})
    rig.run.mkdir()
    (rig.run / f"{companion_id(UDID)}.pid").write_text(f"6001 {OWNER} {TAG}")
    started = await rig.launcher.start(BINARY, UDID)
    assert rig.signals[0] == (6001, signal.SIGTERM) and started.pid_file.read_text() == f"4242 {OWNER} {TAG}"


async def test_starting_ends_a_companion_whose_owner_is_gone(tmp_path: Path) -> None:
    rig = Rig(tmp_path, alive={6001}, commands={6001: "idb_companion --udid x"})
    rig.run.mkdir()
    (rig.run / f"{companion_id(UDID)}.pid").write_text(f"6001 {OTHER} OtherHost")
    started = await rig.launcher.start(BINARY, UDID)
    assert rig.signals[0] == (6001, signal.SIGTERM)
    assert started.pid_file.read_text() == f"4242 {OWNER} {TAG}"


@pytest.mark.parametrize("content", ["", "   ", "abc 777", "5001 owner", f"\n5001 {OTHER} {TAG}"])
async def test_an_unreadable_pid_file_is_tidied_and_never_signals(tmp_path: Path, content: str) -> None:
    rig = Rig(tmp_path, alive={5001, OTHER}, commands={5001: BINARY})
    rig.run.mkdir()
    (rig.run / "odd.pid").write_text(content)
    assert await rig.launcher.reap_orphans() == 0
    assert rig.signals == [] and list(rig.run.iterdir()) == []


def test_the_launcher_speaks_for_this_process_unless_told_otherwise_and_its_tag_is_one_word(tmp_path: Path) -> None:
    launcher = CompanionLauncher(run_dir=tmp_path, log_dir=tmp_path, owner_tag=TAG)
    assert launcher._owner == os.getpid()
    for bad in ("", "two words"):
        with pytest.raises(ValueError, match="one word"):
            CompanionLauncher(run_dir=tmp_path, log_dir=tmp_path, owner_tag=bad)


def _executable(path: Path) -> str:
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return str(path)


def test_the_configured_companion_is_used_only_when_it_runs(tmp_path: Path) -> None:
    good = _executable(tmp_path / "idb_companion")
    plain = tmp_path / "plain"
    plain.write_text("")
    assert find_companion(good) == good
    assert find_companion(str(plain), which=lambda name: good) is None
    assert find_companion(str(tmp_path / "missing"), which=lambda name: good) is None


def test_an_unset_companion_is_found_on_path_then_where_homebrew_puts_it(tmp_path: Path) -> None:
    on_path = _executable(tmp_path / "on-path")
    brew = _executable(tmp_path / "brew")
    assert find_companion("", candidates=(brew,), which=lambda name: on_path) == on_path
    assert find_companion("", candidates=(str(tmp_path / "nope"), brew), which=lambda name: None) == brew
    os.chmod(on_path, 0o644)
    assert find_companion("", candidates=(brew,), which=lambda name: on_path) == brew
    assert find_companion("", candidates=(), which=lambda name: None) is None
    assert companion_module.COMPANION_CANDIDATES[0].startswith("/opt/homebrew")


@pytest.mark.parametrize(
    ("code", "out", "version"),
    [
        (0, '{"build_date":"Sep 11 2026","build_time":"15:34:20"}\n', "Sep 11 2026 15:34:20"),
        (0, "{}", None),
        (0, "1.5.9\n", "1.5.9"),
        (0, '["x"]', '["x"]'),
        (0, "", None),
        (1, "boom", None),
    ],
)
async def test_the_companion_version_is_what_it_prints(code: int, out: str, version: str | None) -> None:
    seen: list[Sequence[str]] = []

    async def run(argv: Sequence[str]) -> tuple[int, str]:
        seen.append(argv)
        return code, out

    assert await companion_version(BINARY, run=run) == version
    assert seen == [(BINARY, "--version")]


def test_every_readable_pid_file_in_a_run_folder_is_listed_whether_or_not_its_companion_runs(tmp_path: Path) -> None:
    (tmp_path / "a.pid").write_text(f"5001 {OWNER} {TAG}\n{XCODE_27}")
    (tmp_path / "b.pid").write_text("5002")
    (tmp_path / "c.pid").write_text("garbage")
    (tmp_path / "d.sock").touch()
    assert recorded_companions(tmp_path) == [Recorded(5001, OWNER, TAG, XCODE_27), Recorded(5002, None, None, None)]
    assert recorded_companions(tmp_path / "missing") == []
