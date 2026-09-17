# SPDX-License-Identifier: Apache-2.0
"""The native helper's life: found, asked its version, started in its own folder with its flags, and ended."""

from __future__ import annotations

import signal
from collections.abc import Sequence
from pathlib import Path

import pytest

from sim_mirror._version import __version__
from sim_mirror.connectors.helper_process import helper_id
from sim_mirror.connectors.native import helper as helper_module
from sim_mirror.connectors.native.helper import (
    PROGRAM,
    HelperLauncher,
    HelperUnavailable,
    HelperVersion,
    SelfCheck,
    SelfCheckPart,
    built_helper,
    find_helper,
    helper_argv,
    helper_sources,
    helper_version,
    self_check,
)
from sim_mirror.testing.fakes import BOOTED_UDID, SCREEN
from sim_mirror.testing.native import FakeHelperSpawn, short_run_dir

XCODE_27 = "/Applications/Xcode27.app/Contents/Developer"


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


def test_the_helper_ships_beside_the_package_and_is_built_per_version(tmp_path: Path) -> None:
    assert helper_module.PACKAGED.parts[-3:] == ("sim_mirror", "_bin", PROGRAM)
    assert built_helper(tmp_path) == tmp_path / "helpers" / f"native-{__version__}" / PROGRAM
    assert built_helper(tmp_path, "9.9.9").parent.name == "native-9.9.9"


def test_the_configured_helper_is_used_only_when_it_runs_else_the_first_candidate_that_does(tmp_path: Path) -> None:
    shipped = tmp_path / "shipped" / PROGRAM
    built = _executable(tmp_path / "built" / PROGRAM)
    plain = tmp_path / "plain"
    plain.write_text("")
    assert find_helper("", (shipped, built)) == str(built)
    assert find_helper(str(built), ()) == str(built)
    assert find_helper(str(plain), (built,)) is None
    assert find_helper("", (shipped, plain)) is None


@pytest.mark.parametrize(
    ("code", "out", "version"),
    [
        (
            0,
            f'{{"core_simulator":"1171.7","version":"{__version__}","wire":1}}',
            HelperVersion(__version__, 1, "1171.7"),
        ),
        (0, '{"core_simulator":null,"version":"0.9.0","wire":2}', HelperVersion("0.9.0", 2, None)),
        (0, '{"version":"1.0.0"}', None),
        (0, "[1]", None),
        (0, "not json", None),
        (2, "usage", None),
    ],
)
async def test_a_helpers_version_is_what_it_prints(code: int, out: str, version: HelperVersion | None) -> None:
    seen: list[Sequence[str]] = []

    async def run(argv: Sequence[str]) -> tuple[int, str]:
        seen.append(argv)
        return code, out

    assert await helper_version("/bin/h", run=run) == version
    assert seen == [("/bin/h", "version")]


def test_only_a_helper_of_this_version_and_wire_is_usable() -> None:
    assert HelperVersion(__version__, 1, None).usable
    assert not HelperVersion("0.1.0", 1, None).usable
    assert not HelperVersion(__version__, 2, None).usable


def test_a_helper_is_told_its_device_socket_parent_input_and_idle_frames() -> None:
    assert helper_argv("/h", "U", Path("/r/s.sock"), parent=42, hid="indigo", idle_key_frames=False) == (
        "/h", "serve", "--udid", "U", "--socket", "/r/s.sock", "--parent-pid", "42", "--hid", "indigo",
        "--idle-key-frames", "off",
    )  # fmt: skip
    assert helper_argv("/h", "U", Path("/s"), parent=1, hid="auto", idle_key_frames=True)[-1] == "on"


async def test_a_helper_starts_in_its_own_folder_answers_and_ends(tmp_path: Path) -> None:
    spawn = FakeHelperSpawn()
    with short_run_dir() as run:
        launcher = HelperLauncher(
            run_dir=run,
            log_dir=tmp_path / "logs",
            owner_tag="SimMirrorTest",
            spawn=spawn,
            signal_group=spawn.signal_group,
            pid_alive=lambda pid: False,
            owner=777,
        )
        running = await launcher.start("/bin/helper", BOOTED_UDID, XCODE_27, hid="dtuhid", ready_timeout_s=3)
        try:
            name = helper_id(BOOTED_UDID)
            assert running.socket == run / "native" / f"{name}.sock" and running.alive
            argv, log = spawn.started[0]
            assert argv == helper_argv(
                "/bin/helper", BOOTED_UDID, running.socket, parent=777, hid="dtuhid", idle_key_frames=True
            )
            assert log == tmp_path / "logs" / f"native-{name}.log"
            assert spawn.envs[0] is not None and spawn.envs[0]["DEVELOPER_DIR"] == XCODE_27
            assert running.pid_file.read_text() == f"7000 777 SimMirrorTest\n{XCODE_27}"
            assert await running.engine.describe() == SCREEN
        finally:
            await launcher.stop(running)
        assert not running.alive and running.process.returncode == -signal.SIGTERM
        assert not running.socket.exists() and not running.pid_file.exists()


async def test_a_helper_that_cannot_be_started_is_refused_as_the_helper(tmp_path: Path) -> None:
    spawn = FakeHelperSpawn(fail=OSError("Bad CPU type in executable"))
    with short_run_dir() as run:
        launcher = HelperLauncher(run_dir=run, log_dir=tmp_path, owner_tag="SimMirrorTest", spawn=spawn, owner=777)
        with pytest.raises(HelperUnavailable, match="sim-mirror-helper could not be started: Bad CPU type"):
            await launcher.start("/bin/helper", BOOTED_UDID)


@pytest.mark.parametrize(
    ("out", "found"),
    [
        (
            '{"ok": false, "parts": [{"name": "screen", "ok": true, "detail": "1206x2622"},'
            ' {"name": "input", "ok": false, "detail": "no digitizer"}]}',
            SelfCheck((SelfCheckPart("screen", True, "1206x2622"), SelfCheckPart("input", False, "no digitizer"))),
        ),
        ('{"parts": [{"name": "screen"}]}', None),
        ("[]", None),
        ("", None),
    ],
)
async def test_a_self_check_is_read_part_by_part_whatever_it_exits_with(out: str, found: SelfCheck | None) -> None:
    seen: list[Sequence[str]] = []

    async def run(argv: Sequence[str]) -> tuple[int, str]:
        seen.append(argv)
        return 1, out

    assert await self_check("/bin/h", BOOTED_UDID, XCODE_27, run=run) == found
    assert seen == [("/usr/bin/env", f"DEVELOPER_DIR={XCODE_27}", "/bin/h", "self-check", "--udid", BOOTED_UDID)]
    await self_check("/bin/h", BOOTED_UDID, run=run)
    assert seen[-1] == ("/bin/h", "self-check", "--udid", BOOTED_UDID)
    if found is not None:
        assert not found.ok and SelfCheck(found.parts[:1]).ok


async def test_a_self_check_waits_long_enough_for_a_devices_accessibility_to_wake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[Sequence[str], float]] = []

    async def run(argv: Sequence[str], *, timeout: float) -> tuple[int, str]:
        seen.append((argv, timeout))
        return 0, '{"parts": []}'

    monkeypatch.setattr(helper_module.process, "run", run)
    assert await self_check("/bin/h", BOOTED_UDID) == SelfCheck(())
    assert seen == [(("/bin/h", "self-check", "--udid", BOOTED_UDID), helper_module.SELF_CHECK_TIMEOUT_S)]


def test_the_swift_sources_are_the_first_folder_with_a_package(tmp_path: Path) -> None:
    empty, package = tmp_path / "wheel", tmp_path / "checkout"
    empty.mkdir()
    package.mkdir()
    (package / "Package.swift").write_text("// swift-tools-version:6.0\n")
    assert helper_sources((empty, package)) == package
    assert helper_sources((empty,)) is None
    assert helper_module.SOURCE_CANDIDATES[0].parts[-2:] == ("sim_mirror", "_helper_src")
    assert helper_sources() == helper_module.SOURCE_CANDIDATES[1]
