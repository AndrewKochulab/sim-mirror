# SPDX-License-Identifier: Apache-2.0
"""Which apps on a simulator say they share their hierarchy, and which listings are passed over as untrustworthy."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from sim_mirror.connectors.app import discovery, wire
from sim_mirror.connectors.app.discovery import AppListing, find_listings, read_listing
from sim_mirror.testing.app_sdk import BUNDLE_ID, SECRET, write_listing

UDID = "7A4C5B2E-9E2B-4C43-9F3A-2D0C3F0B6E11"
UID = os.getuid()


def listing(**more: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "protocol": 1, "sdk_version": "1.0.0", "device_udid": UDID, "bundle_id": BUNDLE_ID, "name": "AppSDK",
        "pid": 4242, "port": 51234, "secret": SECRET, "active": True, "started_at": "2026-09-17T08:00:00.000Z",
    }  # fmt: skip
    raw.update(more)
    return raw


def test_a_listing_reads_as_what_it_says_and_never_shows_its_secret(tmp_path: Path) -> None:
    path = write_listing(tmp_path, listing())
    found = read_listing(path, UDID, uid=UID)
    assert found == AppListing(
        path=path,
        modified_ns=path.stat().st_mtime_ns,
        protocol=1,
        sdk_version="1.0.0",
        bundle_id=BUNDLE_ID,
        name="AppSDK",
        pid=4242,
        port=51234,
        active=True,
        secret=SECRET,
    )
    assert SECRET not in repr(found)
    unnamed = read_listing(write_listing(tmp_path, listing(name="", active="yes")), UDID, uid=UID)
    assert unnamed is not None and (unnamed.name, unnamed.active) == (BUNDLE_ID, False)
    long_named = read_listing(write_listing(tmp_path, listing(name="A" * 300)), UDID, uid=UID)
    assert long_named is not None and long_named.name == "A" * wire.NAME_MAX


@pytest.mark.parametrize(
    "more",
    [
        {"device_udid": "11111111-2222-3333-4444-555555555555"},
        {"protocol": 0},
        {"protocol": True},
        {"protocol": "1"},
        {"pid": 0},
        {"port": 80},
        {"port": 70000},
        {"secret": "short"},
        {"secret": "q2Vn1c7yJx0mH4tR8bW3sK6pZ9dL5fA2gE7uY1oN3iC\r\nX-Evil: 1"},
        {"secret": None},
        {"sdk_version": "1.0.0 beta"},
        {"sdk_version": 1},
        {"bundle_id": 7},
    ],
)
def test_a_listing_that_does_not_say_what_a_listing_says_is_passed_over(tmp_path: Path, more: dict[str, Any]) -> None:
    raw = listing(**more)
    folder = tmp_path.joinpath(*wire.LISTINGS)
    folder.mkdir(parents=True)
    path = folder / f"{BUNDLE_ID}.json"
    path.write_text(json.dumps(raw))
    path.chmod(0o600)
    assert read_listing(path, UDID, uid=UID) is None


def test_a_listing_named_for_another_app_or_with_a_path_in_its_name_is_passed_over(tmp_path: Path) -> None:
    path = write_listing(tmp_path, listing())
    renamed = path.with_name("com.example.other.json")
    path.rename(renamed)
    assert read_listing(renamed, UDID, uid=UID) is None
    sneaky = listing(bundle_id="../../escape")
    folder = tmp_path.joinpath(*wire.LISTINGS)
    (folder / "escape.json").write_text(json.dumps(sneaky))
    (folder / "escape.json").chmod(0o600)
    assert read_listing(folder / "escape.json", UDID, uid=UID) is None


def test_only_a_small_private_regular_file_of_this_users_is_believed(tmp_path: Path) -> None:
    path = write_listing(tmp_path, listing())
    assert read_listing(path, UDID, uid=UID + 1) is None, "another user's file"
    path.chmod(0o644)
    assert read_listing(path, UDID, uid=UID) is None, "a file others can read"
    path.chmod(0o600)
    link = path.with_name("io.github.andrewkochulab.link.json")
    link.symlink_to(path)
    assert read_listing(link, UDID, uid=UID) is None, "a link"
    fifo = path.with_name("io.github.andrewkochulab.fifo.json")
    os.mkfifo(fifo, 0o600)
    assert read_listing(fifo, UDID, uid=UID) is None, "not a regular file, and reading it never blocks"
    path.write_text(json.dumps(listing(name="x" * wire.LISTING_MAX_BYTES)))
    assert read_listing(path, UDID, uid=UID) is None, "too large"
    path.write_text("{not json")
    assert read_listing(path, UDID, uid=UID) is None
    path.write_text("[1, 2]")
    assert read_listing(path, UDID, uid=UID) is None
    assert read_listing(path.with_name("gone.json"), UDID, uid=UID) is None


def test_a_file_that_changes_once_it_is_opened_is_passed_over(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = write_listing(tmp_path, listing())
    real = os.fstat

    def swapped(descriptor: int) -> os.stat_result:
        info = real(descriptor)
        return os.stat_result((info.st_mode | 0o044, *tuple(info)[1:]))

    monkeypatch.setattr(discovery.os, "fstat", swapped)
    assert read_listing(path, UDID, uid=UID) is None


def test_the_apps_still_running_are_found_newest_first_once_each_wherever_they_listed(tmp_path: Path) -> None:
    alive = {101, 102, 104}
    shared = write_listing(tmp_path, listing(bundle_id="com.example.first", pid=101))
    os.utime(shared, ns=(1, 3_000))
    container = tmp_path.joinpath(*wire.CONTAINERS, "0F2B-APP")
    own = write_listing(container, listing(bundle_id="com.example.second", pid=102))
    os.utime(own, ns=(1, 2_000))
    stale_twin = write_listing(
        tmp_path.joinpath(*wire.CONTAINERS, "0F2B-OLD"), listing(bundle_id="com.example.first", pid=104)
    )
    os.utime(stale_twin, ns=(1, 1_000))
    gone = write_listing(
        tmp_path.joinpath(*wire.CONTAINERS, "0F2B-GONE"), listing(bundle_id="com.example.gone", pid=103)
    )
    os.utime(gone, ns=(1, 4_000))
    (tmp_path.joinpath(*wire.LISTINGS) / ".com.example.first.json.101.tmp").write_text("{}")
    (tmp_path.joinpath(*wire.LISTINGS) / "notes.txt").write_text("")
    found = find_listings(tmp_path, UDID, pid_alive=alive.__contains__, uid=UID)
    assert [(each.bundle_id, each.pid) for each in found] == [("com.example.first", 101), ("com.example.second", 102)]
    assert find_listings(tmp_path / "nowhere", UDID, uid=UID) == []


def test_how_many_listings_are_read_is_capped_and_a_folder_that_cannot_be_read_is_passed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wire, "LISTINGS_MAX", 2)
    for n in range(4):
        path = write_listing(tmp_path, listing(bundle_id=f"com.example.app{n}", pid=os.getpid()))
        os.utime(path, ns=(1, n * 1_000))
    blocked = tmp_path.joinpath(*wire.CONTAINERS, "BLOCKED", *wire.LISTINGS)
    blocked.parent.mkdir(parents=True)
    blocked.write_text("a file where a folder should be")
    found = find_listings(tmp_path, UDID)
    assert [each.bundle_id for each in found] == ["com.example.app3", "com.example.app2"]


def test_a_listing_gone_before_it_is_read_sorts_last(tmp_path: Path) -> None:
    assert discovery._modified(tmp_path / "gone.json") == 0
