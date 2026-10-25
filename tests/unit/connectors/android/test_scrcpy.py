# SPDX-License-Identifier: Apache-2.0
"""scrcpy's server: the release SimMirror speaks to and fetching it, its protocol, and the screen, input and text it
gives an Android session."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterable, AsyncIterator, Sequence
from pathlib import Path

import pytest

from sim_mirror.connectors.android import scrcpy
from sim_mirror.connectors.android.geometry import Rotation
from sim_mirror.connectors.android.scrcpy_roles import ScrcpyControl, ScrcpyInput, ScrcpyScreen, ScrcpyText
from sim_mirror.connectors.android.scrcpy_source import (
    PINNED,
    Hashes,
    ScrcpyJars,
    ScrcpySourceError,
    download,
)
from sim_mirror.connectors.android.screen import AdbScreen
from sim_mirror.connectors.base import ConnectorError, HidEvent, Screen
from sim_mirror.core import gestures
from sim_mirror.core.frames import is_sync_point
from sim_mirror.testing.adb import FakeAdbServer
from sim_mirror.testing.scrcpy import IDR, P_FRAME, TEST_JAR, TEST_RELEASE, FakeScrcpy, fetched_jars

PIXEL = Screen(1080, 2424, 411, 923, 2.625)
SERIAL = "emulator-5554"


def fixed(quarters: int = 0) -> Rotation:
    async def ask() -> str:
        return f"mCurrentOrientation={quarters}"

    return Rotation(ask)


async def stream(events: Sequence[HidEvent]) -> AsyncIterable[HidEvent]:
    for event in events:
        yield event


# -- the release ------------------------------------------------------------------------------------------------------


def test_the_pinned_release_is_githubs_own_asset() -> None:
    assert PINNED.version == "5.0.1" and PINNED.size == 733930
    assert PINNED.url == "https://github.com/Genymobile/scrcpy/releases/download/v5.0.1/scrcpy-server-v5.0.1"
    assert PINNED.file_name == "scrcpy-server-v5.0.1"


def test_a_server_is_used_only_when_it_is_a_release_simmirror_speaks_to(tmp_path: Path) -> None:
    jars = fetched_jars(tmp_path / "fetched")
    found = jars.find()
    assert found is not None and found.release == TEST_RELEASE and found.path.read_bytes() == TEST_JAR
    assert jars.why_not() is None
    mine = tmp_path / "mine"
    mine.write_bytes(TEST_JAR)
    assert jars.find(str(mine)) == type(found)(mine, TEST_RELEASE)
    other = tmp_path / "other"
    other.write_bytes(b"something else")
    assert jars.find(str(other)) is None
    assert "is not a scrcpy server SimMirror knows" in (jars.why_not(str(other)) or "")
    empty = fetched_jars(tmp_path / "empty", fetched=False)
    assert empty.find() is None and "is not set up" in (empty.why_not() or "")


def test_a_hash_is_read_again_only_once_the_file_changed(tmp_path: Path) -> None:
    file = tmp_path / "jar"
    file.write_bytes(b"one")
    hashes = Hashes()
    assert hashes.of(file) == hashlib.sha256(b"one").hexdigest()
    file.write_bytes(b"two!")
    assert hashes.of(file) == hashlib.sha256(b"two!").hexdigest()
    assert hashes.of(tmp_path / "none") is None
    file.chmod(0)
    hashes._known.clear()
    try:
        assert hashes.of(file) is None
    finally:
        file.chmod(0o644)


def test_a_fetched_server_that_is_not_the_release_is_thrown_away(tmp_path: Path) -> None:
    jars = ScrcpyJars(root=lambda: tmp_path, fetch=lambda url: b"tampered", release=TEST_RELEASE)
    with pytest.raises(ScrcpySourceError, match=r"is not release 9\.9\.9"):
        jars.fetch_now()
    assert not any(tmp_path.iterdir())


async def test_a_person_s_setup_fetches_once_says_how_it_stands_and_tells_who_waits(tmp_path: Path) -> None:
    told: list[int] = []
    jars = fetched_jars(tmp_path, fetched=False)
    jars.on_ready = lambda: told.append(1)
    fetching = jars.start()
    assert jars.start() is fetching and fetching.state == "fetching" and "9.9.9" in fetching.said
    assert fetching.task is not None
    await fetching.task
    assert fetching.state == "ready" and told == [1] and jars.find() is not None
    failing = ScrcpyJars(root=lambda: tmp_path / "f", fetch=lambda url: b"x", release=TEST_RELEASE)
    failed = failing.start()
    assert failed.task is not None
    await failed.task
    assert failed.state == "failed" and "thrown away" in failed.said
    await failing.shutdown()


async def test_a_fetch_under_way_ends_with_the_host(tmp_path: Path) -> None:
    import time

    jars = ScrcpyJars(root=lambda: tmp_path, fetch=lambda url: time.sleep(0.2) or TEST_JAR, release=TEST_RELEASE)  # type: ignore[func-returns-value]
    jars.start()
    await jars.shutdown()
    assert jars.fetching is not None and jars.fetching.task is not None and jars.fetching.task.cancelled()
    await ScrcpyJars(root=lambda: tmp_path).shutdown()


def test_a_download_is_refused_past_its_size_or_when_it_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    class Answer:
        def __init__(self, data: bytes) -> None:
            self.data = data

        def __enter__(self) -> Answer:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def read(self, most: int) -> bytes:
            return self.data[:most]

    from sim_mirror.connectors.android import scrcpy_source

    monkeypatch.setattr(scrcpy_source.urllib.request, "urlopen", lambda url, timeout: Answer(b"jar"))
    assert download("https://x") == b"jar"
    monkeypatch.setattr(scrcpy_source, "JAR_MAX", 2)
    with pytest.raises(ScrcpySourceError, match="larger than"):
        download("https://x")

    def refuse(url: str, timeout: float) -> Answer:
        raise OSError("offline")

    monkeypatch.setattr(scrcpy_source.urllib.request, "urlopen", refuse)
    with pytest.raises(ScrcpySourceError, match="could not be fetched from https://x: offline"):
        download("https://x")


# -- the protocol -----------------------------------------------------------------------------------------------------


def test_a_server_runs_as_its_own_pid_with_simmirrors_options() -> None:
    assert scrcpy.remote_jar("SimMirror Test") == "/data/local/tmp/sim-mirror-scrcpy-SimMirror-Test.jar"
    assert scrcpy.remote_jar("") == "/data/local/tmp/sim-mirror-scrcpy-host.jar"
    command = scrcpy.server_command("/data/local/tmp/x.jar", "5.0.1", 0xABC, {"video": "false"})
    assert command[:6] == ["env", "CLASSPATH=/data/local/tmp/x.jar", "app_process", "/", scrcpy.SERVER_CLASS, "5.0.1"]
    assert "scid=00000abc" in command and "cleanup=false" in command and "video=false" in command
    options = scrcpy.video_options(max_size=1083, fps=30, bitrate=2_000_000, key_frame_s=1.0)
    assert options["max_size"] == "1080" and options["video_codec_options"] == "i-frame-interval:float=1"
    assert (
        options["capture_orientation"] == "@0"
        and scrcpy.video_options(max_size=3, fps=1, bitrate=1, key_frame_s=1)["max_size"] == "8"
    )


def test_control_messages_are_laid_out_as_the_server_reads_them() -> None:
    assert scrcpy.key(scrcpy.ACTION_DOWN, 3) == bytes([0, 0]) + (3).to_bytes(4, "big") + bytes(8)
    assert scrcpy.text("hi") == bytes([1]) + (2).to_bytes(4, "big") + b"hi"
    assert (
        scrcpy.clipboard(7, "é", paste=True)
        == bytes([9]) + (7).to_bytes(8, "big") + b"\x01" + (2).to_bytes(4, "big") + "é".encode()
    )
    down = scrcpy.touch(scrcpy.ACTION_DOWN, 10, 20, 1080, 2424)
    assert len(down) == 32 and down[:2] == bytes([2, 0]) and down[-10:-8] == b"\xff\xff"
    assert scrcpy.touch(scrcpy.ACTION_UP, 10, 20, 1080, 2424)[-10:-8] == b"\x00\x00"
    assert scrcpy.reset_video() == bytes([17])


async def units_of(data: bytes) -> list[tuple[bytes, int, int]]:
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    found = []
    with pytest.raises(ConnectorError):
        async for unit in scrcpy.h264_units(reader):
            found.append(unit)
    return found


async def test_video_is_frames_each_key_frame_with_the_parameter_sets_in_front() -> None:
    config = b"\x00\x00\x00\x01\x67\x01\x00\x00\x00\x01\x68\x02"
    data = (
        (scrcpy.H264).to_bytes(4, "big")
        + scrcpy.HEADER.pack(scrcpy.SESSION_FLAG | 1080, 2424)
        + scrcpy.HEADER.pack(scrcpy.CONFIG_FLAG, len(config)) + config
        + scrcpy.HEADER.pack(scrcpy.KEY_FRAME_FLAG | 5, len(IDR)) + IDR
        + scrcpy.HEADER.pack(9, len(P_FRAME)) + P_FRAME
        + scrcpy.HEADER.pack(scrcpy.SESSION_FLAG | 1080, 2424)
    )  # fmt: skip
    units = await units_of(data)
    assert units == [(config + IDR, 1080, 2424), (P_FRAME, 1080, 2424)]
    assert is_sync_point(units[0][0]) and not is_sync_point(units[1][0])


async def test_video_that_is_not_h264_or_changes_size_or_is_too_large_is_refused() -> None:
    with pytest.raises(ConnectorError, match=r"not H\.264"):
        async for _ in scrcpy.h264_units(_reader(b"\x00avc1")):
            pass
    resized = (scrcpy.H264).to_bytes(4, "big") + scrcpy.HEADER.pack(scrcpy.SESSION_FLAG | 1080, 2424)
    resized += scrcpy.HEADER.pack(scrcpy.SESSION_FLAG | 720, 1616)
    with pytest.raises(scrcpy.SizeChanged, match="720x1616"):
        async for _ in scrcpy.h264_units(_reader(resized)):
            pass
    huge = (scrcpy.H264).to_bytes(4, "big") + scrcpy.HEADER.pack(0, scrcpy.PACKET_MAX + 1)
    with pytest.raises(ConnectorError, match="sent a packet of"):
        async for _ in scrcpy.h264_units(_reader(huge)):
            pass


def _reader(data: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return reader


@pytest.fixture
async def server() -> AsyncIterator[FakeAdbServer]:
    async with FakeAdbServer() as running:
        yield running.emulator()


async def test_a_server_is_pushed_once_and_started_and_reached_and_ended(server: FakeAdbServer) -> None:
    FakeScrcpy(server)
    launcher = scrcpy.ScrcpyLauncher(server.adb(), "host-a", scid=lambda: 0xABC)
    await launcher.push(SERIAL, TEST_JAR, 1_700_000_000)
    await launcher.push(SERIAL, TEST_JAR, 1_700_000_000)
    assert sum(1 for asked, service in server.calls if service == "sync:") == 3, "a stat, a push, then a stat alone"
    assert server.files[(SERIAL, launcher.remote)] == TEST_JAR
    started = await launcher.start(SERIAL, "9.9.9", scrcpy.CONTROL_OPTIONS)
    assert started.running and len(started.sockets) == 1
    command = next(iter(server.running.values()))
    assert "scid=00000abc" in command and command.startswith("env CLASSPATH=/data/local/tmp/sim-mirror-scrcpy-host-a")
    await started.close()
    await started.close()
    assert server.running == {} and not started.running


async def test_a_server_that_does_not_start_or_open_its_sockets_says_why(server: FakeAdbServer) -> None:
    launcher = scrcpy.ScrcpyLauncher(server.adb(), "host-a", start_timeout=0.05)
    with pytest.raises(ConnectorError, match="did not start: it did not open its socket"):
        await launcher.start(SERIAL, "9.9.9", scrcpy.CONTROL_OPTIONS)
    server.on("env CLASSPATH", code=1, err="[server] ERROR: The server version (9.9.9) does not match\n")
    with pytest.raises(ConnectorError, match="does not match"):
        await launcher.start(SERIAL, "9.9.9", scrcpy.CONTROL_OPTIONS)
    with pytest.raises(ConnectorError, match="could not be started"):
        await launcher.start("emulator-9", "9.9.9", scrcpy.CONTROL_OPTIONS)


async def test_a_video_server_whose_control_socket_does_not_open_is_ended(server: FakeAdbServer) -> None:
    opened = []

    async def once(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        opened.append(1)
        del server.abstract["scrcpy_"]
        await asyncio.sleep(1)

    server.abstract["scrcpy_"] = once
    launcher = scrcpy.ScrcpyLauncher(server.adb(), "host-a")
    with pytest.raises(ConnectorError, match="did not open its control socket"):
        await launcher.start(SERIAL, "9.9.9", {"video": "true", "control": "true"}, sockets=2)
    assert server.running == {}


async def test_this_hosts_servers_left_behind_are_ended(server: FakeAdbServer) -> None:
    launcher = scrcpy.ScrcpyLauncher(server.adb(), "host-a")
    server.on("pkill", code=1)
    assert await launcher.reap([SERIAL, "emulator-9"]) == 0
    server.on("pkill")
    assert await launcher.reap([SERIAL]) == 1


# -- what a session does with it ------------------------------------------------------------------------------------


async def test_the_screen_streams_live_and_asks_for_a_key_frame_when_none_has_come(server: FakeAdbServer) -> None:
    fake = FakeScrcpy(server)
    launcher = scrcpy.ScrcpyLauncher(server.adb(), "host-a")
    shots = AdbScreen(server.adb(), SERIAL, PIXEL, fixed())
    screen = ScrcpyScreen(shots, launcher, SERIAL, "9.9.9")
    assert await screen.describe() == PIXEL and (await screen.screenshot(max_width=50, quality=70)).width == 50
    units = []
    async for unit in screen.h264(fps=30, scale=0.5, key_frame_s=0.25, bitrate=1_000_000):
        units.append(unit)
        if len(units) >= 25:
            break
    assert is_sync_point(units[0]) and fake.resets >= 1
    started = next(command for asked, command in server.calls if command.startswith("shell,v2,raw:echo pid"))
    assert "max_size=1208" in started and "max_fps=30" in started and "control=true" in started
    await asyncio.sleep(0.05)
    assert server.running == {}, "the stream's server ends with it"


async def test_a_screen_encoded_at_another_size_ends_its_stream_and_its_session(server: FakeAdbServer) -> None:
    fake = FakeScrcpy(server)
    fake.resize_to = (720, 1616)
    resized: list[int] = []
    screen = ScrcpyScreen(
        AdbScreen(server.adb(), SERIAL, PIXEL, fixed()),
        scrcpy.ScrcpyLauncher(server.adb(), "host-a"),
        SERIAL,
        "9.9.9",
        on_resized=lambda: resized.append(1),
    )
    with pytest.raises(scrcpy.SizeChanged):
        async for _ in screen.h264(fps=30, scale=1, key_frame_s=1, bitrate=1):
            pass
    assert resized == [1]


async def control_of(server: FakeAdbServer) -> ScrcpyControl:
    launcher = scrcpy.ScrcpyLauncher(server.adb(), "host-a")
    return ScrcpyControl(await launcher.start(SERIAL, "9.9.9", scrcpy.CONTROL_OPTIONS))


async def settled(fake: FakeScrcpy, count: int) -> None:
    for _ in range(100):
        if len(fake.messages) >= count:
            return
        await asyncio.sleep(0.01)


async def test_a_finger_is_sent_down_moving_and_up_in_the_displays_pixels(server: FakeAdbServer) -> None:
    fake = FakeScrcpy(server)
    control = await control_of(server)
    sink = ScrcpyInput(control, PIXEL, fixed())
    await sink.hid(
        stream([HidEvent.touch("down", 100, 200), HidEvent.touch("down", 100, 300), HidEvent.touch("up", 100, 300)])
    )
    await settled(fake, 3)
    assert [message.fields[:3] for message in fake.messages] == [(0, 262, 525), (2, 262, 788), (1, 262, 788)]
    turned = ScrcpyInput(control, PIXEL, fixed(1))
    await turned.hid(stream([HidEvent.touch("down", 0, 0), HidEvent.touch("up", 0, 0)]))
    await settled(fake, 5)
    assert fake.messages[3].fields[1:5] == (0, 1079, 2424, 1080), "turned, the display is wider than tall"
    await control.close()


async def test_buttons_and_keys_are_androids_keys_with_command_as_control(server: FakeAdbServer) -> None:
    fake = FakeScrcpy(server)
    control = await control_of(server)
    sink = ScrcpyInput(control, PIXEL, fixed())
    await sink.hid(stream([event for _at, event in gestures.button("back")]))
    await sink.hid(stream([event for _at, event in gestures.select_all()]))
    await settled(fake, 4)
    assert [message.fields for message in fake.messages] == [(0, 4, 0), (1, 4, 0), (0, 29, 0x1000), (1, 29, 0x1000)]
    with pytest.raises(ConnectorError, match="no siri button"):
        await sink.hid(stream([HidEvent.press("siri", "down")]))
    with pytest.raises(ConnectorError, match="key 240 has no Android key"):
        await sink.hid(stream([HidEvent.key(240, "down")]))
    await control.close()


async def test_text_is_typed_as_it_is_or_pasted_whole(server: FakeAdbServer) -> None:
    fake = FakeScrcpy(server)
    control = await control_of(server)
    typer = ScrcpyText(control)
    await typer.type("hello " * 60)
    await typer.type("café ☕\nnext")
    await settled(fake, 3)
    assert [message.kind for message in fake.messages] == ["text", "text", "clipboard"]
    assert fake.messages[0].fields == ("hello " * 50,) and fake.messages[2].fields == ("café ☕\nnext", True)
    await control.close()
    with pytest.raises(ConnectorError, match="control socket closed"):
        await typer.type("again")
    assert not control.running


async def test_a_setup_no_one_waits_for_still_ends_ready(tmp_path: Path) -> None:
    jars = fetched_jars(tmp_path, fetched=False)
    fetching = jars.start()
    assert fetching.task is not None
    await fetching.task
    assert fetching.state == "ready"


async def test_the_stand_in_server_names_a_message_it_does_not_know(server: FakeAdbServer) -> None:
    fake = FakeScrcpy(server)
    control = await control_of(server)
    await control.send(bytes([99]))
    await settled(fake, 1)
    assert fake.messages[-1].kind == "unknown 99"
    await control.close()
