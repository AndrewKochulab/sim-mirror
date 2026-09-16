# SPDX-License-Identifier: Apache-2.0
"""The bridge client: one mcpbridge on the scope's Xcode, each answer matched to its call, refusals and stops said."""

from __future__ import annotations

import asyncio
import gc
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from sim_mirror._version import __version__
from sim_mirror.connectors.mcpbridge import client as client_module
from sim_mirror.connectors.mcpbridge.client import (
    PROTOCOL_VERSION,
    BridgeClient,
    BridgeError,
    BridgeRefused,
    find_bridge,
    said,
    spawn_bridge,
)
from sim_mirror.testing.fakes import FakeBridge, FakeBridgeProcess, FakeXcrun, fixture_json

XCODE_27 = "/Applications/Xcode27.app/Contents/Developer"


def bridge_client(bridge: FakeBridge, developer_dir: str = XCODE_27) -> BridgeClient:
    return BridgeClient(developer_dir, spawn=bridge.spawn)


async def test_a_call_starts_the_bridge_with_the_scopes_xcode_says_hello_once_and_answers_what_xcode_did(
    tmp_path: Path,
) -> None:
    bridge = FakeBridge(folder=tmp_path)
    client = bridge_client(bridge)
    assert not client.alive
    started = await client.call("DeviceInteractionStartSession", {"deviceIdentifier": "D", "sessionIdentifier": "S"},
                                timeout=5)  # fmt: skip
    ended = await client.call("DeviceInteractionEndSession", {"interactionSessionKey": "S"}, timeout=5)
    assert started["interactionSessionKey"] == "S" and ended == {"userMessage": "Session stopped"}
    (process,) = bridge.processes
    assert process.argv[1:] == ("mcpbridge",) and process.env["DEVELOPER_DIR"] == XCODE_27
    assert client.alive and client.developer_dir == XCODE_27
    await client.close()
    assert not client.alive and process.returncode == 1 and not process.killed


async def test_the_hello_names_simmirror_and_the_protocol_version_it_speaks(tmp_path: Path) -> None:
    sent: list[dict[str, object]] = []
    bridge = FakeBridge(folder=tmp_path)
    receive = bridge.receive

    def record(process: FakeBridgeProcess, message: dict[str, object]) -> None:
        sent.append(message)
        receive(process, message)

    bridge.receive = record  # type: ignore[method-assign]
    client = BridgeClient("", spawn=bridge.spawn, client_name="Host")
    await client.call("XcodeListWorkspaces", {}, timeout=5)
    assert [message["method"] for message in sent] == ["initialize", "notifications/initialized", "tools/call"]
    assert sent[0]["params"] == {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "Host", "version": __version__},
    }
    assert bridge.processes[0].env.get("DEVELOPER_DIR") == os.environ.get("DEVELOPER_DIR")
    await client.close()


async def test_what_xcode_refuses_is_said_in_its_own_words(tmp_path: Path) -> None:
    bridge = FakeBridge(folder=tmp_path)
    client = bridge_client(bridge)
    not_approved = fixture_json("mcpbridge-answers.json")["not_approved"]["result"]
    bridge.reply("DeviceInteractionStartSession", not_approved)
    with pytest.raises(BridgeRefused) as refused:
        await client.call("DeviceInteractionStartSession", {"deviceIdentifier": "D", "sessionIdentifier": "S"},
                          timeout=5)  # fmt: skip
    assert refused.value.tool == "DeviceInteractionStartSession"
    assert refused.value.text.startswith("This agent isn't approved to use Xcode's tools yet.")
    bridge.refuse("DeviceInteractionSynthesize", "Session not found. It may have already been closed")
    with pytest.raises(BridgeRefused, match="Xcode refused DeviceInteractionSynthesize: Session not found"):
        await client.call("DeviceInteractionSynthesize", {"interactSessionKey": "S"}, timeout=5)
    await client.close()


@pytest.mark.parametrize(
    ("result", "reason"),
    [
        (
            {"content": [{"type": "text", "text": "plain words"}]},
            "the answer had nothing SimMirror can read: plain words",
        ),
        (
            {"content": "odd", "structuredContent": [1]},
            "the answer had nothing SimMirror can read: no reason was given",
        ),
    ],
)
async def test_an_answer_with_nothing_to_read_is_refused(
    tmp_path: Path, result: dict[str, object], reason: str
) -> None:
    bridge = FakeBridge(folder=tmp_path).reply("XcodeListWorkspaces", result)
    client = bridge_client(bridge)
    with pytest.raises(BridgeRefused) as refused:
        await client.call("XcodeListWorkspaces", {}, timeout=5)
    assert refused.value.text == reason
    await client.close()


async def test_a_json_rpc_error_is_refused_with_its_message(tmp_path: Path) -> None:
    bridge = FakeBridge(folder=tmp_path)
    client = bridge_client(bridge)
    await client.call("XcodeListWorkspaces", {}, timeout=5)
    process = bridge.processes[0]
    receive = bridge.receive

    def error(to: FakeBridgeProcess, message: dict[str, object]) -> None:
        if message.get("method") != "tools/call":
            receive(to, message)
        elif message["params"]["name"] == "Unknown":  # type: ignore[index]
            to.say({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32602, "message": "no such tool"}})
        else:
            to.say({"jsonrpc": "2.0", "id": message["id"], "error": "not a mapping"})

    bridge.receive = error  # type: ignore[method-assign]
    with pytest.raises(BridgeRefused, match="Xcode refused Unknown: no such tool"):
        await client.call("Unknown", {}, timeout=5)
    with pytest.raises(BridgeRefused, match="the bridge answered without a result"):
        await client.call("Other", {}, timeout=5)
    assert bridge.processes == [process]
    await client.close()


async def test_a_bridge_that_stops_says_what_it_said_last_and_the_next_call_starts_another(tmp_path: Path) -> None:
    bridge = FakeBridge(folder=tmp_path)
    client = bridge_client(bridge)
    await client.call("XcodeListWorkspaces", {}, timeout=5)
    bridge.stop("mcpbridge/MCPBridge.swift:32: Fatal error: no running Xcode processes found\n")
    with pytest.raises(BridgeError, match=r"mcpbridge stopped: mcpbridge/MCPBridge\.swift:32: Fatal error"):
        await client.call("XcodeListWorkspaces", {}, timeout=5)
    assert not client.alive
    assert await client.call("XcodeListWorkspaces", {}, timeout=5) == {"message": "No workspaces are currently open."}
    assert len(bridge.processes) == 2
    await client.close()


async def test_a_bridge_that_stops_before_its_hello_or_says_nothing_is_refused(tmp_path: Path) -> None:
    bridge = FakeBridge(folder=tmp_path).stop("")
    client = bridge_client(bridge)
    with pytest.raises(BridgeError, match=r"\Amcpbridge stopped\Z"):
        await client.call("XcodeListWorkspaces", {}, timeout=5)
    await client.close()


async def test_a_hello_the_bridge_does_not_accept_is_refused(tmp_path: Path) -> None:
    bridge = FakeBridge(folder=tmp_path)
    bridge.answers["initialize"] = {"jsonrpc": "2.0", "error": {"code": -32600, "message": "bad version"}}
    client = bridge_client(bridge)
    with pytest.raises(BridgeError, match="did not accept SimMirror's hello"):
        await client.call("XcodeListWorkspaces", {}, timeout=5)
    await client.close()


async def test_a_call_not_answered_in_time_ends_the_bridge_and_says_how_long_it_waited(tmp_path: Path) -> None:
    bridge = FakeBridge(folder=tmp_path).reply("DeviceInteractionSynthesize", lambda arguments: None)
    client = bridge_client(bridge)
    unheard: list[dict[str, object]] = []
    asyncio.get_running_loop().set_exception_handler(lambda loop, context: unheard.append(context))
    with pytest.raises(BridgeError, match=r"did not answer DeviceInteractionSynthesize within 0\.05 seconds"):
        await client.call("DeviceInteractionSynthesize", {"interactSessionKey": "S"}, timeout=0.05)
    assert not client.alive and bridge.processes[0].returncode is not None
    gc.collect()
    await asyncio.sleep(0)
    assert unheard == []


async def test_a_bridge_that_does_not_end_when_asked_is_killed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_module, "CLOSE_S", 0.01)
    bridge = FakeBridge(folder=tmp_path)
    bridge.exit_on_close = False
    client = bridge_client(bridge)
    await client.call("XcodeListWorkspaces", {}, timeout=5)
    await client.close()
    await client.close()
    assert bridge.processes[0].killed


async def test_answers_that_are_not_for_a_call_are_ignored_and_a_line_too_long_stops_the_bridge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = FakeBridge(folder=tmp_path)
    client = bridge_client(bridge)
    await client.call("XcodeListWorkspaces", {}, timeout=5)
    process = bridge.processes[0]
    process.stdout.feed_data(b"not json\n")
    process.say({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
    process.say({"jsonrpc": "2.0", "id": "text", "result": {}})
    process.say({"jsonrpc": "2.0", "id": 999, "result": {}})
    process.stdout.feed_data(b"[1, 2]\n")
    assert await client.call("XcodeListWorkspaces", {}, timeout=5) == {"message": "No workspaces are currently open."}

    def overrun() -> None:
        raise ValueError("Separator is not found, and chunk exceed the limit")

    async def readline() -> bytes:
        overrun()
        return b""

    process.stdout.readline = readline  # type: ignore[method-assign]
    process.stdout.feed_data(b"{}\n")
    await asyncio.sleep(0)
    with pytest.raises(BridgeError):
        await client.call("XcodeListWorkspaces", {}, timeout=0.2)
    await client.close()


async def test_a_bridge_that_cannot_be_written_to_or_started_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = FakeBridge(folder=tmp_path)
    client = bridge_client(bridge)
    await client.call("XcodeListWorkspaces", {}, timeout=5)
    bridge.processes[0].stdin.closed = True
    with pytest.raises(BridgeError, match="mcpbridge stopped"):
        await client._send({"jsonrpc": "2.0", "method": "ping"})
    await client.close()
    with pytest.raises(BridgeError, match="mcpbridge stopped"):
        await client._send({"jsonrpc": "2.0", "method": "ping"})

    async def cannot(argv: Sequence[str], env: Mapping[str, str]) -> FakeBridgeProcess:
        raise PermissionError("not allowed")

    with pytest.raises(BridgeError, match="mcpbridge could not be started: not allowed"):
        await BridgeClient(spawn=cannot).call("XcodeListWorkspaces", {}, timeout=5)
    monkeypatch.setattr(client_module, "xcrun_binary", lambda: None)
    with pytest.raises(BridgeError, match=r"no xcrun"):
        await BridgeClient(spawn=bridge.spawn).call("XcodeListWorkspaces", {}, timeout=5)


async def test_the_real_spawn_starts_the_program_with_pipes_in_its_own_group() -> None:
    # `cat` stands in for mcpbridge: it answers a line with the same line, and ends when stdin closes.
    process = await spawn_bridge(["/bin/cat"], {"PATH": "/bin"})
    process.stdin.write(b'{"id": 1}\n')
    await process.stdin.drain()
    assert json.loads(await process.stdout.readline()) == {"id": 1}
    process.stdin.close()
    assert await process.wait() == 0


async def test_only_an_xcode_from_27_with_mcpbridge_counts() -> None:
    xcrun = FakeXcrun().with_xcode("27.0")
    assert await find_bridge(XCODE_27, xcrun) == "/Applications/Xcode27.app/Contents/Developer/usr/bin/mcpbridge"
    assert xcrun.calls[0].developer_dir == XCODE_27
    assert await find_bridge("", FakeXcrun().with_xcode("26.6", "17F42")) is None
    assert await find_bridge("", FakeXcrun().with_xcode("27.0", bridge=False)) is None
    assert await find_bridge("", FakeXcrun().on("--find", "mcpbridge", out="/x/mcpbridge")) is None


async def test_calls_already_answered_are_not_failed_when_the_bridge_stops() -> None:
    client = BridgeClient()
    answered: asyncio.Future[dict[str, object]] = asyncio.get_running_loop().create_future()
    answered.set_result({"id": 1})
    waiting: asyncio.Future[dict[str, object]] = asyncio.get_running_loop().create_future()
    client._pending.update({1: answered, 2: waiting})
    client._fail_pending("stopped")
    assert answered.result() == {"id": 1}
    with pytest.raises(BridgeError, match="stopped"):
        waiting.result()


def test_what_xcode_said_is_its_data_when_the_text_is_json_and_the_text_otherwise() -> None:
    assert said({"content": [{"text": json.dumps({"type": "error", "data": "Invalid command"})}]}) == "Invalid command"
    assert said({"content": [{"text": json.dumps({"type": "error"})}]}) == '{"type": "error"}'
    assert said({"content": [{"text": "x" * 500}]}) == "x" * 400
    assert said({}) == "no reason was given"
