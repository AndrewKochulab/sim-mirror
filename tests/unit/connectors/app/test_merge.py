# SPDX-License-Identifier: Apache-2.0
"""An app's shared hierarchy merged into a device's snapshots, as its scope's settings say, and let go with it."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.app.discovery import AppListing
from sim_mirror.connectors.app.document import SharedApp
from sim_mirror.connectors.app.merge import AppHierarchyMerge, as_hierarchy
from sim_mirror.connectors.app.reader import AppReader
from sim_mirror.perception.readers import NamingReader
from sim_mirror.platform.device_data import DEVICES_DIR_ENV
from sim_mirror.protocol import AppHierarchy
from sim_mirror.testing.app_sdk import FakeAppSdk, app_hierarchy, app_node, write_listing

UDID = "7A4C5B2E-9E2B-4C43-9F3A-2D0C3F0B6E11"
ON = SimConfig.defaults()


def test_the_defaults_merge_an_apps_hierarchy_and_name_what_accessibility_left_unlabeled() -> None:
    assert (ON.app_merge, ON.app_name_unlabeled, ON.app_timeout_ms, ON.app_max_nodes) == (True, True, 500, 3000)


async def test_a_snapshot_reads_the_app_on_the_device_named_by_its_data_folder_and_screens_hear_of_it() -> None:
    shared: list[tuple[str, AppHierarchy | None]] = []
    devices = Path(os.environ[DEVICES_DIR_ENV])
    async with FakeAppSdk(app_hierarchy(app_node("text", "Hello"))) as app:
        write_listing(devices / UDID / "data", app.listing(UDID))
        merge = AppHierarchyMerge(on_share=lambda udid, found: shared.append((udid, found)))
        (reader,) = merge.readers(UDID, "idb", ON)
        assert isinstance(reader, NamingReader) and isinstance(reader.reader, AppReader)
        tree = await reader.read()
    assert [(node.label, node.source) for node in tree.walk()] == [("Hello", "app")]
    assert shared == [(UDID, {"name": "AppSDK", "bundle_id": app.bundle_id, "sdk_version": "1.0.0"})]
    assert app.requests[0].line == "GET /v1/hierarchy?max_nodes=3000 HTTP/1.1"


async def test_the_scopes_settings_are_read_on_every_snapshot() -> None:
    asked: list[tuple[int, float]] = []
    found: list[AppListing] = []

    def find(data_dir: Path, udid: str) -> list[AppListing]:
        assert data_dir == Path("/sims") / udid
        return found

    async def fetch(listing: AppListing, *, max_nodes: int, timeout_s: float) -> dict[str, Any]:
        asked.append((max_nodes, timeout_s))
        return {}

    merge = AppHierarchyMerge(data_dir_for=lambda udid: Path("/sims") / udid, find=find, fetch=fetch)
    (only_adds,) = merge.readers(UDID, "mcpbridge", ON.with_values(app_name_unlabeled=False, app_max_nodes=200,
                                                                   app_timeout_ms=1500))  # fmt: skip
    assert isinstance(only_adds, AppReader)
    found.append(AppListing(Path("/sims/a.json"), 1, 1, "1.0.0", "com.example.a", "A", 1, 1024, True, "s" * 43))
    await only_adds.read()
    assert asked == [(200, 1.5)]


async def test_switched_off_a_device_reads_no_app_and_screens_hear_it_no_longer_shares() -> None:
    shared: list[tuple[str, AppHierarchy | None]] = []
    merge = AppHierarchyMerge(on_share=lambda udid, found: shared.append((udid, found)))
    off = ON.with_values(app_merge=False)
    assert merge.readers(UDID, "idb", off) == ()
    assert shared == [], "nothing was shared, so nothing is said"
    merge.readers(UDID, "idb", ON)
    merge._memory[UDID].shared = SharedApp("Notes", "com.example.notes", "1.0.0")
    assert merge.readers(UDID, "idb", off) == ()
    assert shared == [(UDID, None)]
    merge.readers(UDID, "idb", ON)
    merge.forget(UDID)
    merge.forget("never-seen")
    assert UDID not in merge._memory
    merge.readers(UDID, "idb", ON)
    await merge.close()
    assert merge._memory == {}


def test_a_shared_app_reads_as_the_protocol_says_it() -> None:
    assert as_hierarchy(None) is None
    assert as_hierarchy(SharedApp("Notes", "com.example.notes", "1.0.0")) == {
        "name": "Notes",
        "bundle_id": "com.example.notes",
        "sdk_version": "1.0.0",
    }
    assert AppHierarchyMerge()._on_share(UDID, None) is None
