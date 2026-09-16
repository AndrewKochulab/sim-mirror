# SPDX-License-Identifier: Apache-2.0
"""Merging Xcode's hierarchy into another connector's snapshots: only when asked, one reader per device, let go of."""

from __future__ import annotations

import asyncio
from pathlib import Path

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.mcpbridge.client import BridgeClient
from sim_mirror.connectors.mcpbridge.merge import HierarchyMerge
from sim_mirror.connectors.mcpbridge.reader import BridgeReader
from sim_mirror.host_copy import HostCopy
from sim_mirror.perception.readers import DocumentReader
from sim_mirror.testing.fakes import BOOTED_UDID, FakeBridge

XCODE_27 = "/Applications/Xcode27.app/Contents/Developer"
OTHER_27 = "/Applications/Xcode27-beta.app/Contents/Developer"
ON = SimConfig.defaults().with_values(mcpbridge_merge=True, developer_dir=XCODE_27)


async def found(developer_dir: str) -> str:
    return "/x/mcpbridge"


def merge_on(bridge: FakeBridge, made: list[BridgeReader]) -> HierarchyMerge:
    def reader_for(udid: str, developer_dir: str) -> BridgeReader:
        reader = BridgeReader(udid, developer_dir, client=BridgeClient(developer_dir, spawn=bridge.spawn), find=found)
        made.append(reader)
        return reader

    return HierarchyMerge(reader_for=reader_for)


async def test_nothing_is_merged_unless_the_scope_asks_or_when_mcpbridge_already_drives_the_device(
    tmp_path: Path,
) -> None:
    made: list[BridgeReader] = []
    merge = merge_on(FakeBridge(folder=tmp_path), made)
    assert merge.readers(BOOTED_UDID, "idb", SimConfig.defaults()) == ()
    assert merge.readers(BOOTED_UDID, "mcpbridge", ON) == ()
    assert made == []


async def test_a_device_keeps_one_reader_and_it_reads_xcodes_hierarchy(tmp_path: Path) -> None:
    bridge = FakeBridge(folder=tmp_path)
    made: list[BridgeReader] = []
    merge = merge_on(bridge, made)
    (first,) = merge.readers(BOOTED_UDID, "idb", ON)
    (again,) = merge.readers(BOOTED_UDID, "idb", ON)
    assert isinstance(first, DocumentReader) and first.name == "mcpbridge" and len(made) == 1
    tree = await again.read()
    assert {node.source for node in tree.walk()} == {"mcpbridge"}
    await merge.close()
    assert bridge.tools()[-1] == "DeviceInteractionEndSession"


async def test_turning_it_off_changing_xcode_or_the_device_ending_lets_the_reader_go(tmp_path: Path) -> None:
    bridge = FakeBridge(folder=tmp_path)
    made: list[BridgeReader] = []
    merge = merge_on(bridge, made)
    (reader,) = merge.readers(BOOTED_UDID, "idb", ON)
    await reader.read()
    assert merge.readers(BOOTED_UDID, "idb", ON.with_values(mcpbridge_merge=False)) == ()
    await merge.close()
    assert bridge.tools().count("DeviceInteractionEndSession") == 1
    (reader,) = merge.readers(BOOTED_UDID, "idb", ON)
    await reader.read()
    merge.readers(BOOTED_UDID, "idb", ON.with_values(developer_dir=OTHER_27))
    assert [each.developer_dir for each in made] == [XCODE_27, XCODE_27, OTHER_27]
    merge.forget(BOOTED_UDID)
    merge.forget(BOOTED_UDID)
    await asyncio.sleep(0)
    await merge.close()
    assert bridge.tools().count("DeviceInteractionEndSession") == 2


async def test_a_merge_made_with_defaults_makes_readers_for_the_host() -> None:
    merge = HierarchyMerge(copy=HostCopy(owner_name="Host"))
    (reader,) = merge.readers(BOOTED_UDID, "idb", ON)
    assert isinstance(reader, DocumentReader)
    await merge.close()
