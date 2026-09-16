# SPDX-License-Identifier: Apache-2.0
"""The bridge reader: one session per device, a capture per read, its files removed, Xcode's refusals said plainly."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from sim_mirror.connectors.base import ConnectorError
from sim_mirror.connectors.mcpbridge.client import BridgeClient, BridgeError
from sim_mirror.connectors.mcpbridge.hierarchy import document_from_hierarchy
from sim_mirror.connectors.mcpbridge.reader import BridgeReader
from sim_mirror.host_copy import HostCopy
from sim_mirror.testing.fakes import BOOTED_UDID, FakeBridge, fixture, fixture_json

XCODE_27 = "/Applications/Xcode27.app/Contents/Developer"
BRIDGE = f"{XCODE_27}/usr/bin/mcpbridge"


async def found(developer_dir: str) -> str | None:
    return BRIDGE


def reader_on(bridge: FakeBridge, *, find: Any = found, copy: HostCopy | None = None) -> BridgeReader:
    tokens = iter(f"t{n}" for n in range(1, 10))
    return BridgeReader(
        BOOTED_UDID,
        XCODE_27,
        copy=copy,
        client=BridgeClient(XCODE_27, spawn=bridge.spawn),
        find=find,
        token=lambda: next(tokens),
    )


async def test_reads_share_one_session_and_leave_none_of_xcodes_files_behind(tmp_path: Path) -> None:
    bridge = FakeBridge(folder=tmp_path)
    reader = reader_on(bridge)
    first = await reader.accessibility()
    second = await reader.accessibility()
    assert first == second == document_from_hierarchy(fixture("mcpbridge-hierarchy-settings.txt"))
    key = "SimMirror D946616B t1"
    assert bridge.calls == [
        ("DeviceInteractionStartSession", {"deviceIdentifier": BOOTED_UDID, "sessionIdentifier": key}),
        ("DeviceInteractionSynthesize", {"interactSessionKey": key}),
        ("DeviceInteractionSynthesize", {"interactSessionKey": key}),
    ]
    assert list(tmp_path.iterdir()) == []
    await reader.close()
    assert bridge.tools()[-1] == "DeviceInteractionEndSession" and bridge.processes[0].returncode is not None
    assert reader.udid == BOOTED_UDID and reader.developer_dir == XCODE_27


async def test_a_session_left_without_reads_is_ended_so_xcode_can_give_the_device_to_another(tmp_path: Path) -> None:
    bridge = FakeBridge(folder=tmp_path)
    naps: list[float] = []
    wake = asyncio.Event()

    async def sleep(seconds: float) -> None:
        naps.append(seconds)
        await wake.wait()

    tokens = iter(["t1", "t2"])
    reader = BridgeReader(BOOTED_UDID, XCODE_27, client=BridgeClient(XCODE_27, spawn=bridge.spawn), find=found,
                          token=lambda: next(tokens), idle_s=45.0, sleep=sleep)  # fmt: skip
    await reader.accessibility()
    await reader.accessibility()
    await asyncio.sleep(0)
    assert naps == [45.0] and bridge.tools().count("DeviceInteractionEndSession") == 0
    wake.set()
    for _ in range(20):
        await asyncio.sleep(0)
    assert bridge.tools()[-1] == "DeviceInteractionEndSession" and bridge.processes[0].returncode is not None
    wake.clear()
    await reader.accessibility()
    starts = [arguments["sessionIdentifier"] for tool, arguments in bridge.calls if tool.endswith("StartSession")]
    assert starts == ["SimMirror D946616B t1", "SimMirror D946616B t2"] and len(bridge.processes) == 2
    await reader.close()
    assert bridge.tools().count("DeviceInteractionEndSession") == 2


async def test_a_session_xcode_lost_is_opened_again_under_a_new_name_once(tmp_path: Path) -> None:
    bridge = FakeBridge(folder=tmp_path)
    reader = reader_on(bridge)
    await reader.accessibility()
    bridge.refuse("DeviceInteractionSynthesize", "Session not found. It may have already been closed")
    await reader.accessibility()
    starts = [arguments["sessionIdentifier"] for tool, arguments in bridge.calls if tool.endswith("StartSession")]
    assert starts == ["SimMirror D946616B t1", "SimMirror D946616B t2"]
    bridge.refuse("DeviceInteractionSynthesize", "Session not found.")
    bridge.refuse("DeviceInteractionStartSession", "This session identifier is currently in use or was recently used.")
    with pytest.raises(ConnectorError, match="Xcode could not keep a session on this device: This session identifier"):
        await reader.accessibility()
    await reader.close()


IN_USE = (
    "The target device is already in use by a different session with key '{key}'. If that session is no longer "
    "needed, stop it first and retry."
)


async def test_a_session_simmirror_left_on_the_device_is_ended_and_another_agents_is_left_be(tmp_path: Path) -> None:
    bridge = FakeBridge(folder=tmp_path)
    bridge.refuse("DeviceInteractionStartSession", IN_USE.format(key="SimMirror D946616B 68b057fe"))
    reader = reader_on(bridge)
    await reader.accessibility()
    assert bridge.calls[:3] == [
        (
            "DeviceInteractionStartSession",
            {"deviceIdentifier": BOOTED_UDID, "sessionIdentifier": "SimMirror D946616B t1"},
        ),
        ("DeviceInteractionEndSession", {"interactionSessionKey": "SimMirror D946616B 68b057fe"}),
        (
            "DeviceInteractionStartSession",
            {"deviceIdentifier": BOOTED_UDID, "sessionIdentifier": "SimMirror D946616B t2"},
        ),
    ]
    await reader.close()
    other = FakeBridge(folder=tmp_path).refuse("DeviceInteractionStartSession", IN_USE.format(key="Verify Login Flow"))
    reader = reader_on(other)
    with pytest.raises(ConnectorError) as refused:
        await reader.accessibility()
    assert str(refused.value) == (
        "Xcode's tools already have a session on this simulator ('Verify Login Flow'), and it can have one at a time: "
        "end that session where it was started, then read the screen again."
    )
    assert other.tools() == ["DeviceInteractionStartSession"]
    await reader.close()
    twice = FakeBridge(folder=tmp_path)
    for _ in range(2):
        twice.refuse("DeviceInteractionStartSession", IN_USE.format(key="SimMirror D946616B old"))
    reader = reader_on(twice)
    with pytest.raises(ConnectorError, match="already have a session on this simulator"):
        await reader.accessibility()
    await reader.close()


async def test_an_agent_xcode_has_not_approved_is_told_how_to_have_it_approved(tmp_path: Path) -> None:
    bridge = FakeBridge(folder=tmp_path)
    bridge.reply("DeviceInteractionStartSession", fixture_json("mcpbridge-answers.json")["not_approved"]["result"])
    reader = reader_on(bridge, copy=HostCopy(owner_name="Host", xcode_approve_command="host xcode approve"))
    with pytest.raises(ConnectorError) as refused:
        await reader.accessibility()
    assert str(refused.value) == (
        "Xcode has not approved Host to use its tools yet. Xcode approves an agent that opens a project through them: "
        "run `host xcode approve /path/to/App.xcodeproj` once, and allow it if Xcode asks."
    )
    await reader.close()
    assert "DeviceInteractionEndSession" not in bridge.tools()


async def test_an_xcode_before_27_is_refused_before_anything_is_started_and_asked_only_until_it_is_found(
    tmp_path: Path,
) -> None:
    bridge = FakeBridge(folder=tmp_path)
    asked: list[str] = []
    answers = iter([None, BRIDGE])

    async def find(developer_dir: str) -> str | None:
        asked.append(developer_dir)
        return next(answers)

    reader = reader_on(bridge, find=find)
    with pytest.raises(ConnectorError, match="needs Xcode 27 or later; the Xcode in use at /Applications/Xcode27"):
        await reader.accessibility()
    assert bridge.processes == []
    await reader.accessibility()
    await reader.accessibility()
    assert asked == [XCODE_27, XCODE_27]
    await reader.close()


@pytest.mark.parametrize(
    ("data", "said"),
    [
        ("The task timed out. Please try again.", "Xcode could not read the screen: The task timed out."),
        ("The device simulator cannot be connected.", "Xcode could not read the screen: The device simulator cannot"),
    ],
)
async def test_what_else_xcode_refuses_is_passed_on(tmp_path: Path, data: str, said: str) -> None:
    bridge = FakeBridge(folder=tmp_path).refuse("DeviceInteractionSynthesize", data)
    reader = reader_on(bridge)
    with pytest.raises(ConnectorError) as refused:
        await reader.accessibility()
    assert str(refused.value).startswith(said)
    await reader.close()


async def test_a_bridge_that_is_missing_or_stops_is_said_so(tmp_path: Path) -> None:
    bridge = FakeBridge(folder=tmp_path).stop('xcrun: error: unable to find utility "mcpbridge", not a developer tool')
    reader = reader_on(bridge)
    with pytest.raises(ConnectorError, match="needs Xcode 27 or later"):
        await reader.accessibility()
    bridge.stop("Fatal error: something else")
    with pytest.raises(BridgeError, match="mcpbridge stopped: Fatal error: something else"):
        await reader.accessibility()
    await reader.close()


async def test_a_session_xcode_opened_without_a_key_is_refused(tmp_path: Path) -> None:
    bridge = FakeBridge(folder=tmp_path).reply("DeviceInteractionStartSession", lambda arguments: {
        "content": [], "structuredContent": {"interactionSessionKey": ""}})  # fmt: skip
    reader = reader_on(bridge)
    with pytest.raises(ConnectorError, match="Xcode opened no session on this device"):
        await reader.accessibility()
    await reader.close()


def capture(**paths: str) -> dict[str, Any]:
    return {"content": [], "structuredContent": {"applicationState": "NotRun", **paths}}


@pytest.mark.parametrize(
    "paths",
    [
        {},
        {"hierarchyPath": "relative/SimMirror D946616B t1-hierarchy.txt"},
        {"hierarchyPath": "/somewhere/else-hierarchy.txt"},
        {"hierarchyPath": "/somewhere/SimMirror D946616B t1-hierarchy.json"},
    ],
)
async def test_a_capture_that_names_no_hierarchy_of_this_session_is_refused_and_nothing_is_removed(
    tmp_path: Path, paths: dict[str, str]
) -> None:
    bridge = FakeBridge(folder=tmp_path).reply("DeviceInteractionSynthesize", lambda arguments: capture(**paths))
    reader = reader_on(bridge)
    with pytest.raises(ConnectorError, match="named no hierarchy it wrote"):
        await reader.accessibility()
    await reader.close()


async def test_only_files_of_this_capture_beside_the_hierarchy_are_removed(tmp_path: Path) -> None:
    key = "SimMirror D946616B t1"
    folder = tmp_path / "DeviceInteractionSynthesize"
    elsewhere = tmp_path / "elsewhere"
    folder.mkdir()
    elsewhere.mkdir()
    hierarchy = folder / f"{key}-1-hierarchy.txt"
    hierarchy.write_text(fixture("mcpbridge-hierarchy-alert.txt"))
    screenshot = folder / f"{key}-1-screenshot.png"
    someone_elses = folder / "Other Session-1-logs.txt"
    outside = elsewhere / f"{key}-1-thumbnailScreenshot.png"
    for path in (screenshot, someone_elses, outside):
        path.write_bytes(b"")
    bridge = FakeBridge(folder=tmp_path).reply(
        "DeviceInteractionSynthesize",
        lambda arguments: capture(
            hierarchyPath=str(hierarchy),
            screenshotPath=str(screenshot),
            logsPath=str(someone_elses),
            thumbnailScreenshotPath=str(outside),
        ),
    )
    reader = reader_on(bridge)
    document = await reader.accessibility()
    assert document["modal"] == {"type": "Alert", "label": "Alert title, with 'quotes'"}
    assert sorted(path.name for path in folder.iterdir()) == ["Other Session-1-logs.txt"] and outside.exists()
    await reader.close()


async def test_a_hierarchy_that_cannot_be_read_is_refused_and_its_files_still_removed(tmp_path: Path) -> None:
    key = "SimMirror D946616B t1"
    missing = tmp_path / f"{key}-1-hierarchy.txt"
    screenshot = tmp_path / f"{key}-1-screenshot.png"
    screenshot.write_bytes(b"")
    bridge = FakeBridge(folder=tmp_path).reply(
        "DeviceInteractionSynthesize",
        lambda arguments: capture(hierarchyPath=str(missing), screenshotPath=str(screenshot)),
    )
    reader = reader_on(bridge)
    with pytest.raises(ConnectorError, match="the hierarchy Xcode wrote could not be read: No such file"):
        await reader.accessibility()
    assert not screenshot.exists()
    await reader.close()


async def test_closing_a_reader_that_never_read_or_whose_bridge_stopped_ends_nothing_in_xcode(tmp_path: Path) -> None:
    bridge = FakeBridge(folder=tmp_path)
    await reader_on(bridge).close()
    assert bridge.processes == []
    reader = reader_on(bridge)
    await reader.accessibility()
    bridge.processes[0].finish(1)
    await reader.close()
    assert "DeviceInteractionEndSession" not in bridge.tools()


async def test_a_reader_made_with_defaults_names_its_bridge_for_the_host() -> None:
    reader = BridgeReader(BOOTED_UDID, copy=HostCopy(owner_name="Host"))
    assert reader.developer_dir == "" and reader._client._client_name == "Host"
