# SPDX-License-Identifier: Apache-2.0
"""The Android emulator: how it is started, how SimMirror knows the ones it started, and its console."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from sim_mirror.platform.emulator import (
    EmulatorConsole,
    EmulatorError,
    EmulatorOptions,
    Emulators,
    Started,
    argv,
    free_port,
    port_of,
    read_token,
)
from sim_mirror.testing.adb import FakeEmulatorConsole

EMULATOR = Path("/sdk/emulator/emulator")


def test_an_avd_is_started_headless_on_its_port_unless_its_window_is_asked_for() -> None:
    assert argv(EMULATOR, "Pixel_9", 5554, EmulatorOptions()) == [
        "/sdk/emulator/emulator", "-avd", "Pixel_9", "-port", "5554", "-no-window", "-no-audio", "-no-boot-anim",
        "-gpu", "auto",
    ]  # fmt: skip
    shown = argv(EMULATOR, "Pixel_9", 5556, EmulatorOptions(window=True, gpu="host", cold_boot=True))
    assert "-no-window" not in shown and shown[-3:] == ["-gpu", "host", "-no-snapshot-load"]
    assert argv(EMULATOR, "Pixel_9", 5556, EmulatorOptions(gpu="anything"))[-1] == "auto"


def test_a_port_is_one_no_running_emulator_has_and_names_its_serial() -> None:
    assert free_port([]) == 5554
    assert free_port([5554, 5555, 5557]) == 5558
    with pytest.raises(EmulatorError, match="every emulator port"):
        free_port(range(5554, 5682))
    assert port_of("emulator-5556") == 5556
    assert port_of("emulator-x") is None and port_of("R58M12ABC") is None
    assert Started(1, "Pixel_9", 5560).serial == "emulator-5560"


class Spawned:
    def __init__(self, pid: int = 4321) -> None:
        self.pid = pid
        self.calls: list[tuple[Sequence[str], Path]] = []

    async def __call__(self, command: Sequence[str], log: Path, **options: Any) -> Any:
        self.calls.append((command, log))
        return self


async def test_an_emulator_this_host_started_is_known_again_while_it_runs(tmp_path: Path) -> None:
    emulator = tmp_path / "emulator"
    emulator.write_text("")
    spawned = Spawned()
    running = {4321: f"{emulator} -avd Pixel_9 -port 5554 -no-window"}

    async def command_of(pid: int) -> str | None:
        return running.get(pid)

    made = Emulators(
        tmp_path / "run", tmp_path / "logs", "host-a", spawn=spawned, alive=running.__contains__, command_of=command_of
    )
    started = await made.start(emulator, "Pixel_9", 5554, EmulatorOptions())
    assert started == Started(4321, "Pixel_9", 5554)
    assert spawned.calls[0][1] == tmp_path / "logs" / "emulator-Pixel_9.log"
    assert json.loads((tmp_path / "run" / "Pixel_9.json").read_text()) == {
        "pid": 4321, "owner": "host-a", "avd": "Pixel_9", "port": 5554,
    }  # fmt: skip
    assert await made.started_here("Pixel_9") == started
    other_host = Emulators(tmp_path / "run", tmp_path / "logs", "host-b", alive=running.__contains__)
    assert await other_host.started_here("Pixel_9") is None, "another host's emulator is never read as this one's"
    assert await made.started_here("Medium_Phone") is None
    running[4321] = "/usr/bin/vim notes.txt"
    assert await made.started_here("Pixel_9") is None, "a pid reused by another program is not the emulator"
    assert not (tmp_path / "run" / "Pixel_9.json").exists()


async def test_a_stale_or_unreadable_pid_file_is_let_go(tmp_path: Path) -> None:
    folder = tmp_path / "run"
    folder.mkdir()
    (folder / "Dead.json").write_text(json.dumps({"pid": 99, "owner": "host-a", "avd": "Dead", "port": 5554}))
    (folder / "Broken.json").write_text("{")
    (folder / "Odd.json").write_text(json.dumps({"pid": "x", "owner": "host-a"}))
    made = Emulators(folder, tmp_path / "logs", "host-a", alive=lambda pid: False)
    assert await made.started_here("Dead") is None and not (folder / "Dead.json").exists()
    assert await made.started_here("Broken") is None
    assert await made.started_here("Odd") is None
    made.forget("Never")


async def test_an_emulator_that_is_not_installed_or_will_not_start_says_so(tmp_path: Path) -> None:
    made = Emulators(tmp_path / "run", tmp_path / "logs", "host-a")
    with pytest.raises(EmulatorError, match="not installed at"):
        await made.start(tmp_path / "missing", "Pixel_9", 5554, EmulatorOptions())
    emulator = tmp_path / "emulator"
    emulator.write_text("")

    async def refuse(command: Sequence[str], log: Path, **options: Any) -> Any:
        raise PermissionError("not executable")

    with pytest.raises(EmulatorError, match="could not be started: not executable"):
        await Emulators(tmp_path / "run", tmp_path / "logs", "host-a", spawn=refuse).start(
            emulator, "Pixel_9", 5554, EmulatorOptions()
        )


def test_the_console_token_is_read_from_its_file_and_is_empty_when_there_is_none(tmp_path: Path) -> None:
    token = tmp_path / "token"
    token.write_text("abc123\n")
    assert read_token(token) == "abc123"
    assert read_token(tmp_path / "none") == ""


async def test_the_console_is_asked_after_its_token_and_answers_each_command() -> None:
    async with FakeEmulatorConsole("Pixel_9", token="secret") as console:
        reached = EmulatorConsole(console.port, token=lambda: "secret")
        assert await reached.avd_name() == "Pixel_9"
        await reached.geo_fix(52.52, 13.405)
        await reached.kill()
        assert console.commands == ["avd name", "geo fix 13.405000 52.520000", "kill"] and console.killed


async def test_a_wrong_token_or_a_refused_command_says_why() -> None:
    async with FakeEmulatorConsole(token="secret") as console:
        with pytest.raises(EmulatorError, match="authentication token does not match"):
            await EmulatorConsole(console.port, token=lambda: "wrong").avd_name()
        with pytest.raises(EmulatorError, match="unknown command"):
            await EmulatorConsole(console.port, token=lambda: "").avd_name()
        console.answers["geo"] = "KO: bad coordinates"
        reached = EmulatorConsole(console.port, token=lambda: "secret")
        with pytest.raises(EmulatorError, match="refused it: bad coordinates"):
            await reached.geo_fix(1, 2)
        console.answers["avd"] = "Pixel_9\nline two"
        assert await reached.avd_name() == "Pixel_9\nline two"
        with pytest.raises(EmulatorError, match="a console command is one line"):
            await reached.command("avd name\nkill")


async def test_a_console_that_does_not_answer_or_says_too_much_is_refused() -> None:
    with pytest.raises(EmulatorError, match="does not answer"):
        await EmulatorConsole(1, token=lambda: "", timeout=1).avd_name()

    async def chatty(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(b"x" * 70_000 + b"\r\n")
        await writer.drain()
        writer.close()

    async def long_winded(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write((b"y" * 1000 + b"\r\n") * 70)
        await writer.drain()
        writer.close()

    async def mute(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.close()

    for handler, said in (
        (chatty, "more than a console answer holds"),
        (long_winded, "more than a console answer holds"),
        (mute, "does not answer"),
    ):
        served = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = served.sockets[0].getsockname()[1]
        with pytest.raises(EmulatorError, match=said):
            await EmulatorConsole(port, token=lambda: "").avd_name()
        served.close()
        await served.wait_closed()


async def test_a_kill_the_console_does_not_answer_is_left_to_the_emulator() -> None:
    await EmulatorConsole(1, token=lambda: "", timeout=1).kill()
