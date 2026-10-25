# SPDX-License-Identifier: Apache-2.0
"""adb's server spoken to directly: devices, shell commands and how they exited, raw output, files, installs, a
device's sockets, and a long-running command ended when let go -- all against a stand-in server."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import pytest

from sim_mirror.platform import adb as adb_module
from sim_mirror.platform.adb import (
    ADB_PORT_ENV,
    ADB_SERVER_ENV,
    Adb,
    AdbDevice,
    AdbDropped,
    AdbError,
    FileStat,
    ShellResult,
    ShellStream,
    command_line,
    open_address,
    parse_devices,
    server_address,
)
from sim_mirror.testing.adb import FakeAdbServer

EMULATOR = "emulator-5554"


@pytest.fixture
async def server() -> AsyncIterator[FakeAdbServer]:
    async with FakeAdbServer() as running:
        running.device(EMULATOR, model="sdk_gphone64_arm64", transport_id="1")
        yield running


def test_a_listing_is_read_device_by_device_and_says_how_each_reaches_the_mac() -> None:
    listed = parse_devices(
        "emulator-5554          device product:sdk_gphone64_arm64 model:sdk_gphone64_arm64 transport_id:1\n"
        "R58M12ABC              unauthorized usb:1-1 transport_id:2\n"
        "192.168.1.20:5555      offline\n"
        "adb-R58M12ABC-x1._adb-tls-connect._tcp device model:Pixel_8\n"
        "\n"
        "lonely\n"
    )
    assert [(d.serial, d.state, d.connection, d.ready) for d in listed] == [
        ("emulator-5554", "device", "emulator", True),
        ("R58M12ABC", "unauthorized", "usb", False),
        ("192.168.1.20:5555", "offline", "network", False),
        ("adb-R58M12ABC-x1._adb-tls-connect._tcp", "device", "network", True),
    ]
    assert listed[0].model == "sdk gphone64 arm64" and listed[1].model == ""


def test_the_server_is_where_simmirror_or_adb_says_else_on_adbs_own_port() -> None:
    assert server_address({ADB_SERVER_ENV: "/tmp/adb.sock", ADB_PORT_ENV: "6000"}) == "/tmp/adb.sock"
    assert server_address({ADB_PORT_ENV: "6000"}) == "127.0.0.1:6000"
    assert server_address({ADB_PORT_ENV: "x"}) == server_address({}) == "127.0.0.1:5037"


def test_a_command_given_as_words_is_quoted_so_none_is_read_as_shell() -> None:
    assert command_line(["am", "start", "-d", "https://a.b/?x=1&y=2"]) == "am start -d 'https://a.b/?x=1&y=2'"
    assert command_line("getprop ro.serialno") == "getprop ro.serialno"


async def test_the_server_says_its_version_and_lists_its_devices(server: FakeAdbServer) -> None:
    adb = server.adb()
    assert await adb.version() == 41
    assert await adb.devices() == [AdbDevice(EMULATOR, "device", {"model": "sdk_gphone64_arm64", "transport_id": "1"})]


async def test_a_shell_command_says_how_it_exited_with_what_it_wrote(server: FakeAdbServer) -> None:
    server.on("getprop", out="36\n").on("ls /nope", code=1, err="ls: /nope: No such file or directory\n")
    adb = server.adb()
    assert await adb.shell(EMULATOR, ["getprop", "ro.build.version.sdk"]) == ShellResult(0, b"36\n", b"")
    assert await adb.run(EMULATOR, "getprop ro.serialno") == "36\n"
    with pytest.raises(AdbError, match="ls: /nope: No such file or directory"):
        await adb.run(EMULATOR, "ls /nope")
    assert server.commands(EMULATOR) == ["getprop ro.build.version.sdk", "getprop ro.serialno", "ls /nope"]
    assert ShellResult(3, b"", b"").message == "it exited with 3"
    assert ShellResult(1, b"a\nb\n", b"").message == "b"


async def test_a_device_that_is_not_there_or_not_ready_is_refused_with_what_adb_said(server: FakeAdbServer) -> None:
    server.device("R58M12ABC", "unauthorized")
    adb = server.adb()
    with pytest.raises(AdbError, match="device 'emulator-9' not found"):
        await adb.shell("emulator-9", "true")
    with pytest.raises(AdbError, match="device unauthorized"):
        await adb.shell("R58M12ABC", "true")


async def test_raw_output_and_input_pass_untouched(server: FakeAdbServer) -> None:
    jpeg = b"\xff\xd8\xff" + bytes(range(256))
    server.on("screencap -j", out=jpeg)
    server.on("cmd package install", out="Success\n")
    adb = server.adb()
    assert await adb.exec_out(EMULATOR, "screencap -j") == jpeg
    assert await adb.exec_out(EMULATOR, "cmd package install -S 3", data=b"abc") == b"Success\n"
    assert server.inputs == [("cmd package install -S 3", b"abc")]


async def test_a_file_is_pushed_and_stated(server: FakeAdbServer) -> None:
    adb = server.adb()
    data = bytes(200_000)
    await adb.push(EMULATOR, data, "/data/local/tmp/x.jar", mtime=7)
    assert server.files[(EMULATOR, "/data/local/tmp/x.jar")] == data
    assert await adb.stat(EMULATOR, "/data/local/tmp/x.jar") == FileStat(0o100644, 200_000, 1_700_000_000)
    missing = await adb.stat(EMULATOR, "/data/local/tmp/y.jar")
    assert missing == FileStat(0, 0, 0) and not missing.exists


async def test_an_app_is_installed_whole_or_from_its_splits_and_a_failure_says_why(
    server: FakeAdbServer, tmp_path: Path
) -> None:
    base, split = tmp_path / "base.apk", tmp_path / "split_config.arm64_v8a.apk"
    base.write_bytes(b"BASE")
    split.write_bytes(b"SPLIT!")
    server.on("cmd package install -r -t -S", out="Success\n")
    server.on("cmd package install-create", out="Success: created install session [1234]\n")
    server.on("cmd package install-write", out="Success: streamed 4 bytes\n")
    server.on("cmd package install-commit", out="Success\n")
    adb = server.adb()
    await adb.install(EMULATOR, [base])
    await adb.install(EMULATOR, [base, split])
    assert server.commands() == [
        "cmd package install -r -t -S 4",
        "cmd package install-create -r -t -S 10",
        "cmd package install-write -S 4 1234 0_base.apk -",
        "cmd package install-write -S 6 1234 1_split_config.arm64_v8a.apk -",
        "cmd package install-commit 1234",
    ]
    assert [data for _command, data in server.inputs] == [b"BASE", b"BASE", b"SPLIT!"]
    server.on("cmd package install -r -t -S", out="Failure [INSTALL_FAILED_OLDER_SDK: needs API 37]\n")
    with pytest.raises(AdbError, match=r"base.apk was not installed: Failure \[INSTALL_FAILED_OLDER_SDK"):
        await adb.install(EMULATOR, [base])
    server.on("cmd package install -r -t -S")
    with pytest.raises(AdbDropped, match="the device said nothing"):
        await adb.install(EMULATOR, [base])
    server.on("cmd package install-create", out="Error: no space\n")
    with pytest.raises(AdbError, match="would not start installing: Error: no space"):
        await adb.install(EMULATOR, [base, split])


async def test_a_devices_abstract_socket_is_opened_through_its_transport(server: FakeAdbServer) -> None:
    async def echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write((await reader.readexactly(5)).upper())
        await writer.drain()

    server.abstract["scrcpy_"] = echo
    adb = server.adb()
    reader, writer = await adb.open(EMULATOR, "localabstract:scrcpy_0000abcd")
    writer.write(b"hello")
    assert await reader.readexactly(5) == b"HELLO"
    writer.close()
    with pytest.raises(AdbError, match="could not connect to localabstract"):
        await adb.open(EMULATOR, "localabstract:nobody")


async def test_devices_are_tracked_as_they_come_and_go(server: FakeAdbServer) -> None:
    adb = server.adb()
    tracked = adb.track()
    assert [device.serial for device in await anext(tracked)] == [EMULATOR]
    server.device("R58M12ABC")
    assert [device.serial for device in await anext(tracked)] == [EMULATOR, "R58M12ABC"]
    server.unplug(EMULATOR)
    assert [device.serial for device in await anext(tracked)] == ["R58M12ABC"]
    await tracked.aclose()


async def test_a_long_running_command_says_its_pid_and_is_ended_when_let_go(server: FakeAdbServer) -> None:
    server.on("app_process", out="[server] INFO: Device: emulator\n")
    adb = server.adb()
    stream = await ShellStream.start(adb, EMULATOR, ["app_process", "/", "com.example.Server"])
    assert (
        stream.pid is not None and stream.running and server.running == {stream.pid: "app_process / com.example.Server"}
    )
    await asyncio.sleep(0.05)
    assert stream.last_said == "[server] INFO: Device: emulator"
    await stream.close()
    await stream.close()
    assert server.running == {} and not stream.running
    assert f"kill {stream.pid}" in server.commands(EMULATOR)


async def test_a_command_that_ends_at_once_says_why_and_is_not_kept(server: FakeAdbServer) -> None:
    server.on("app_process", code=1, err="Aborted\n")
    stream = await ShellStream.start(server.adb(), EMULATOR, ["app_process"])
    await asyncio.sleep(0.05)
    assert not stream.running and stream.exit_code == 1 and stream.last_said == "Aborted"
    await stream.close()
    assert not any(command.startswith("kill") for command in server.commands())


async def test_a_command_that_never_says_its_pid_is_refused(server: FakeAdbServer) -> None:
    async def silent(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await asyncio.sleep(10)

    adb = server.adb()
    real = adb.open

    async def opened(serial: str, service: str) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        return await real(serial, "localabstract:silent")

    server.abstract["silent"] = silent
    adb.open = opened  # type: ignore[method-assign]
    with pytest.raises(AdbError, match="did not start the command: it said nothing"):
        await ShellStream.start(adb, EMULATOR, ["app_process"], timeout=0.05)


async def test_a_server_that_does_not_answer_is_started_once_asked_for_a_device(
    server: FakeAdbServer, tmp_path: Path
) -> None:
    adb_binary = tmp_path / "adb"
    adb_binary.write_text("")
    started: list[Sequence[str]] = []
    up = {"answering": False}
    real = server.address

    async def opener(address: str) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        if not up["answering"]:
            raise ConnectionRefusedError("refused")
        return await open_address(real)

    async def starter(argv: Sequence[str]) -> tuple[int, str]:
        started.append(argv)
        up["answering"] = True
        return 0, ""

    adb = Adb("127.0.0.1:1", opener=opener, adb_path=lambda: adb_binary, starter=starter)
    await adb.ensure_server()
    assert started == [(str(adb_binary), "start-server")]
    await adb.ensure_server()
    assert len(started) == 1, "a server that answers is not started again"


async def test_a_server_that_cannot_be_started_says_why(tmp_path: Path) -> None:
    async def refused(address: str) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        raise ConnectionRefusedError("refused")

    async def failing(argv: Sequence[str]) -> tuple[int, str]:
        return 1, "cannot bind\n"

    no_adb = Adb("127.0.0.1:1", opener=refused)
    with pytest.raises(AdbError, match=r"does not answer at 127\.0\.0\.1:1"):
        await no_adb.ensure_server()
    binary = tmp_path / "adb"
    binary.write_text("")
    with pytest.raises(AdbError, match="could not be started: cannot bind"):
        await Adb("127.0.0.1:1", opener=refused, adb_path=lambda: binary, starter=failing).ensure_server()


async def test_a_server_that_answers_nonsense_is_refused() -> None:
    async def nonsense(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.read(100)
        writer.write(b"WHAT")
        await writer.drain()
        writer.close()

    async def cut(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.read(100)
        writer.write(b"OKAY00")
        await writer.drain()
        writer.close()

    for handler, said in ((nonsense, "answered b'WHAT'"), (cut, "cut short")):
        served = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = served.sockets[0].getsockname()[1]
        with pytest.raises(AdbError, match=said):
            await Adb(f"127.0.0.1:{port}").version()
        served.close()
        await served.wait_closed()


async def test_a_unix_socket_address_is_reached_as_one(tmp_path: Path) -> None:
    with pytest.raises(AdbError, match="does not answer"):
        await Adb(str(tmp_path / "adb.sock")).version()


async def test_the_default_starter_runs_adb_start_server(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[tuple[Sequence[str], float]] = []

    async def run(argv: Sequence[str], *, timeout: float) -> tuple[int, str]:
        ran.append((argv, timeout))
        return 0, ""

    monkeypatch.setattr(adb_module.process, "run", run)
    assert await adb_module.start_server(["/sdk/adb", "start-server"]) == (0, "")
    assert ran == [(["/sdk/adb", "start-server"], adb_module.START_TIMEOUT_S)]


class Scripted:
    """A server that answers whatever a test scripts, after reading what it is sent."""

    def __init__(self, *answers: bytes, close: bool = True) -> None:
        self.answers = answers
        self.close = close

    async def __call__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        for answer in self.answers:
            await reader.read(4096)
            writer.write(answer)
            await writer.drain()
        if self.close:
            writer.close()


async def scripted(*answers: bytes) -> tuple[asyncio.AbstractServer, Adb]:
    served = await asyncio.start_server(Scripted(*answers), "127.0.0.1", 0)
    return served, Adb(f"127.0.0.1:{served.sockets[0].getsockname()[1]}", timeout=2)


@pytest.mark.parametrize(
    ("answers", "call", "said"),
    [
        # The device list stops while it is being tracked.
        ((b"OKAY",), "track", "stopped telling which devices it knows"),
        # The connection closes as the device is asked for.
        ((b"",), "shell", "could not be reached through adb"),
        # The command's output is cut off before it says how it exited.
        ((b"OKAY", b"OKAY\x01\x05\x00\x00\x00ab"), "shell", "stopped answering while it ran getprop"),
        # A file's stat is answered with something else.
        ((b"OKAY", b"OKAY", b"FAIL" + bytes(12)), "stat", "answered b'FAIL' to a file's stat"),
        # A push is refused.
        ((b"OKAY", b"OKAY", b"FAIL\x0b\x00\x00\x00read-only fs"), "push", "could not be written on the device: read"),
        ((b"OKAY", b"OKAY", b"WHAT\x00\x00\x00\x00"), "push", "could not be written on the device: b'WHAT'"),
        # adb refuses with no reason.
        ((b"FAIL0000",), "version", "adb refused host"),
    ],
)
async def test_an_answer_cut_short_or_refused_says_so(answers: tuple[bytes, ...], call: str, said: str) -> None:
    served, adb = await scripted(*answers)
    try:
        with pytest.raises(AdbError, match=said):
            if call == "track":
                await anext(adb.track())
            elif call == "shell":
                await adb.shell(EMULATOR, ["getprop", "x"])
            elif call == "stat":
                await adb.stat(EMULATOR, "/x")
            elif call == "push":
                await adb.push(EMULATOR, b"data", "/x")
            else:
                await adb.version()
    finally:
        served.close()


async def test_a_long_running_command_whose_connection_drops_is_no_longer_running(server: FakeAdbServer) -> None:
    async def first_words(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(b"\x01\x0a\x00\x00\x00not a pid\n")
        await writer.drain()
        writer.close()

    server.abstract["dropped"] = first_words
    adb = server.adb()
    real = adb.open

    async def opened(serial: str, service: str) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        return await real(serial, "localabstract:dropped")

    adb.open = opened  # type: ignore[method-assign]
    with pytest.raises(AdbError, match="did not start the command: not a pid"):
        await ShellStream.start(adb, EMULATOR, ["app_process"], timeout=1)


async def test_the_stand_in_refuses_what_it_does_not_serve_and_runs_a_rules_follow_up(server: FakeAdbServer) -> None:
    adb = server.adb()
    with pytest.raises(AdbError, match="unknown host service host:bogus"):
        await adb._host("host:bogus")
    with pytest.raises(AdbError, match="unknown service bogus:"):
        await adb.open(EMULATOR, "bogus:")
    followed: list[int] = []
    server.on("reboot", then=lambda: followed.append(1))
    await adb.shell(EMULATOR, "reboot")
    await adb.shell(EMULATOR, "kill -9 notapid")
    assert followed == [1]


async def test_a_long_running_command_ends_when_its_connection_is_dropped(server: FakeAdbServer) -> None:
    stream = await ShellStream.start(server.adb(), EMULATOR, ["app_process"])
    assert stream.pid is not None
    stream._writer.close()
    for _ in range(50):
        if stream.pid not in server.running or not stream.running:
            break
        await asyncio.sleep(0.02)
    await stream.close()


async def test_a_packet_of_a_kind_the_shell_does_not_send_is_passed_over() -> None:
    served, adb = await scripted(b"OKAY", b"OKAY\x05\x01\x00\x00\x00x\x01\x02\x00\x00\x00ok\x03\x00\x00\x00\x00")
    try:
        assert await adb.shell(EMULATOR, "true") == ShellResult(0, b"ok", b"")
    finally:
        served.close()


async def test_a_killed_long_running_command_says_it_ended(server: FakeAdbServer) -> None:
    stream = await ShellStream.start(server.adb(), EMULATOR, ["app_process"])
    assert stream.pid is not None
    stream._writer.write(b"x")
    await asyncio.sleep(0.05)
    server.running.pop(stream.pid)
    for _ in range(50):
        if not stream.running:
            break
        await asyncio.sleep(0.02)
    assert stream.exit_code == 0x8F
    await stream.close()


async def test_a_device_whose_connection_dropped_is_waited_for_until_it_is_back(server: FakeAdbServer) -> None:
    adb = server.adb()
    server.device(EMULATOR)
    assert await adb.back(EMULATOR, within=0.5, every=0.01)
    server.device(EMULATOR, "offline")
    asyncio.get_running_loop().call_later(0.05, lambda: server.device(EMULATOR))
    assert await adb.back(EMULATOR, within=2.0, every=0.01)
    server.unplug(EMULATOR)
    assert not await adb.back(EMULATOR, within=0.05, every=0.01)
    adb.address = "127.0.0.1:1"
    assert not await adb.back(EMULATOR, within=0.05, every=0.01)


async def test_a_long_running_command_is_waited_for_to_end_by_itself(server: FakeAdbServer) -> None:
    stream = await ShellStream.start(server.adb(), EMULATOR, ["app_process"])
    assert stream.pid is not None
    assert not await stream.ended(0.05)
    asyncio.get_running_loop().call_later(0.05, lambda: server.running.pop(stream.pid or 0))
    assert await stream.ended(2.0)
    await stream.close()
