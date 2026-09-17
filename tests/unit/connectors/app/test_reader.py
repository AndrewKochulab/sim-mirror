# SPDX-License-Identifier: Apache-2.0
"""The app in front read for its hierarchy: which app is asked, what a snapshot hears, and what is remembered."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from sim_mirror.connectors.app import wire
from sim_mirror.connectors.app.client import fetch_hierarchy
from sim_mirror.connectors.app.discovery import AppListing
from sim_mirror.connectors.app.document import SharedApp
from sim_mirror.connectors.app.errors import AppInactive, AppRefused, AppSdkError, AppTimedOut, AppUnreachable
from sim_mirror.connectors.app.reader import AppMemory, AppReader, ask_apps, probe
from sim_mirror.host_copy import HostCopy
from sim_mirror.perception.model import Modal
from sim_mirror.testing.app_sdk import SECRET, FakeAppSdk, app_hierarchy, app_node, write_listing
from sim_mirror.testing.fakes import ManualClock

UDID = "7A4C5B2E-9E2B-4C43-9F3A-2D0C3F0B6E11"
COPY = HostCopy()


def listing(name: str, *, active: bool = True, written: int = 1, pid: int = 100) -> AppListing:
    return AppListing(
        path=Path(f"/data/{name}.json"),
        modified_ns=written,
        protocol=1,
        sdk_version="1.0.0",
        bundle_id=f"com.example.{name}",
        name=name.title(),
        pid=pid,
        port=50000 + pid,
        active=active,
        secret=SECRET,
    )


def answer(app: AppListing, *nodes: dict[str, Any], **more: Any) -> dict[str, Any]:
    return app_hierarchy(*(nodes or (app_node(label=app.name),)), bundle_id=app.bundle_id, pid=app.pid,
                         name=app.name, **more)  # fmt: skip


class Apps:
    """The apps on a simulator: what each answers, how long it takes, and who was asked."""

    def __init__(self, *listings: AppListing) -> None:
        self.listings = list(listings)
        self.answers: dict[str, dict[str, Any] | BaseException] = {}
        self.delays: dict[str, float] = {}
        self.asked: list[str] = []
        self.cancelled: list[str] = []

    def find(self, data_dir: Path, udid: str) -> list[AppListing]:
        assert (data_dir, udid) == (Path("/data"), UDID)
        return list(self.listings)

    async def fetch(self, listing: AppListing, *, max_nodes: int, timeout_s: float) -> dict[str, Any]:
        self.asked.append(listing.bundle_id)
        try:
            await asyncio.sleep(self.delays.get(listing.bundle_id, 0))
        except asyncio.CancelledError:
            self.cancelled.append(listing.bundle_id)
            raise
        found = self.answers.get(listing.bundle_id, answer(listing))
        if isinstance(found, BaseException):
            raise found
        return found


def reader(
    apps: Apps,
    memory: AppMemory,
    *,
    clock: Callable[[], float] | None = None,
    shared: list[Any] | None = None,
    max_nodes: int = 3000,
) -> AppReader:
    seen = shared if shared is not None else []
    return AppReader(
        UDID,
        data_dir=Path("/data"),
        memory=memory,
        max_nodes=max_nodes,
        timeout_s=0.5,
        copy=COPY,
        on_share=seen.append,
        fetch=apps.fetch,
        find=apps.find,
        clock=clock or ManualClock(),
    )


async def test_the_app_in_front_answers_first_and_the_others_are_let_go() -> None:
    back, front, other = listing("back", active=False, written=9), listing("front", written=1), listing("other", pid=2)
    apps = Apps(back, front, other)
    apps.answers["com.example.front"] = AppInactive("Front is not in front")
    apps.delays["com.example.back"] = 60
    asked = await ask_apps(apps.listings, max_nodes=10, timeout_s=1, fetch=apps.fetch)
    assert asked.listing == other and asked.document is not None and asked.document.app.name == "Other"
    assert [(each.name, type(error)) for each, error in asked.failures] == [("Front", AppInactive)]
    assert apps.asked == ["com.example.front", "com.example.other", "com.example.back"]
    assert apps.cancelled == ["com.example.back"]


async def test_when_no_app_answers_every_reason_is_kept_and_at_most_eight_are_asked() -> None:
    many = [listing(f"app{n}", pid=n + 1, written=n) for n in range(10)]
    apps = Apps(*many)
    for each in many:
        apps.answers[each.bundle_id] = AppUnreachable(f"{each.name} is not listening any more")
    asked = await ask_apps(many, max_nodes=10, timeout_s=1, fetch=apps.fetch)
    assert asked.listing is None and asked.document is None and len(asked.failures) == wire.ASKED_MAX
    assert apps.asked == [f"com.example.app{n}" for n in range(9, 1, -1)]
    assert await ask_apps([], max_nodes=10, timeout_s=1, fetch=apps.fetch) == type(asked)(None, None, ())


async def test_an_answer_from_the_wrong_app_is_a_failure_and_a_bug_is_not_swallowed() -> None:
    mine, theirs = listing("mine"), listing("theirs", pid=7)
    apps = Apps(mine, theirs)
    apps.answers["com.example.mine"] = answer(theirs)
    apps.delays["com.example.theirs"] = 60
    apps.answers["com.example.theirs"] = RuntimeError("never reached")
    asked = await ask_apps([mine], max_nodes=10, timeout_s=1, fetch=apps.fetch)
    assert [type(error) for _, error in asked.failures] == [AppSdkError]
    apps.answers["com.example.mine"] = RuntimeError("a bug")
    with pytest.raises(RuntimeError, match="a bug"):
        await ask_apps([mine, theirs], max_nodes=10, timeout_s=1, fetch=apps.fetch)
    assert apps.cancelled == ["com.example.theirs"]


async def test_a_probe_finds_and_asks_every_app_once_and_says_how_long_it_took(tmp_path: Path) -> None:
    clock = ManualClock()
    async with FakeAppSdk(app_hierarchy(app_node("text", "Hello"))) as app:
        write_listing(tmp_path, app.listing(UDID))

        async def fetch(found: AppListing, *, max_nodes: int, timeout_s: float) -> dict[str, Any]:
            clock.advance(0.04)
            return await fetch_hierarchy(found, max_nodes=max_nodes, timeout_s=timeout_s)

        found = await probe(UDID, tmp_path, max_nodes=100, timeout_s=5, fetch=fetch, clock=clock)
    assert [each.bundle_id for each in found.listings] == [app.bundle_id]
    assert found.asked.document is not None and found.asked.document.elements == 1
    assert found.took_s == pytest.approx(0.04)
    assert app.requests[0].line == "GET /v1/hierarchy?max_nodes=100 HTTP/1.1"


async def test_a_snapshot_reads_the_app_in_front_and_screens_hear_when_it_changes() -> None:
    notes = listing("notes")
    apps = Apps(notes)
    apps.answers[notes.bundle_id] = answer(notes, app_node(label="Save"), modal={"kind": "sheet", "name": "Edit"})
    memory, shared = AppMemory(), []
    tree = await reader(apps, memory, shared=shared).read()
    assert [(node.role, node.label, node.source) for node in tree.walk()] == [("Button", "Save", "app")]
    assert tree.modal == Modal("Edit") and tree.notes == ()
    await reader(apps, memory, shared=shared).read()
    assert shared == [SharedApp("Notes", notes.bundle_id, "1.0.0")] and memory.shared == shared[0]
    apps.listings.clear()
    assert await reader(apps, memory, shared=shared).read() == type(tree)()
    assert shared[-1] is None and memory.shared is None


async def test_a_hierarchy_cut_short_or_from_a_newer_sdk_is_read_and_says_so() -> None:
    notes = listing("notes")
    apps = Apps(notes)
    apps.answers[notes.bundle_id] = answer(notes, protocol=2, truncated=True)
    tree = await reader(apps, AppMemory(), max_nodes=500).read()
    assert tree.notes == (COPY.app_hierarchy_cut("Notes", 500), COPY.app_sdk_newer("Notes", 2))
    assert [node.label for node in tree.walk()] == ["Notes"]


async def test_only_an_app_in_front_that_could_not_be_read_is_worth_a_note() -> None:
    front, gone, back, stale, busy = (
        listing("front", pid=1), listing("gone", pid=2), listing("back", pid=3, active=False),
        listing("stale", pid=4), listing("busy", pid=5),
    )  # fmt: skip
    apps = Apps(front, gone, back, stale, busy)
    apps.answers.update({
        front.bundle_id: AppTimedOut("Front did not answer within 500 ms"),
        gone.bundle_id: AppUnreachable("Gone is not listening any more"),
        back.bundle_id: AppTimedOut("Back did not answer within 500 ms"),
        stale.bundle_id: AppRefused("Stale refused to share its view hierarchy (401 unauthorized)", 401),
        busy.bundle_id: AppRefused("Busy refused to share its view hierarchy (503 busy)", 503),
    })  # fmt: skip
    tree = await reader(apps, AppMemory()).read()
    assert tree.roots == () and tree.notes == (
        COPY.app_hierarchy_unread("Front", "Front did not answer within 500 ms"),
        COPY.app_hierarchy_unread("Busy", "Busy refused to share its view hierarchy (503 busy)"),
    )


async def test_an_app_that_has_gone_or_forgot_its_secret_is_passed_over_until_it_lists_itself_again() -> None:
    gone, stale, wrong, busy, away = (
        listing("gone", pid=1), listing("stale", pid=2), listing("wrong", pid=3), listing("busy", pid=4),
        listing("away", pid=5),
    )  # fmt: skip
    apps = Apps(gone, stale, wrong, busy, away)
    apps.answers.update({
        gone.bundle_id: AppUnreachable("gone"), stale.bundle_id: AppRefused("stale", 401),
        wrong.bundle_id: AppSdkError("wrong"), busy.bundle_id: AppRefused("busy", 503),
        away.bundle_id: AppInactive("away"),
    })  # fmt: skip
    memory = AppMemory()
    await reader(apps, memory).read()
    assert memory.passed == {gone.path: 1, stale.path: 1, wrong.path: 1}
    apps.asked.clear()
    await reader(apps, memory).read()
    assert sorted(apps.asked) == [away.bundle_id, busy.bundle_id]
    apps.listings = [listing("gone", pid=1, written=2), busy, away]
    apps.asked.clear()
    await reader(apps, memory).read()
    assert sorted(apps.asked) == [away.bundle_id, busy.bundle_id, gone.bundle_id]
    assert memory.passed == {gone.path: 2}, "a listing no longer there is forgotten"


async def test_an_app_that_did_not_answer_in_time_is_left_alone_for_a_while_unless_it_lists_itself_again() -> None:
    slow = listing("slow")
    apps = Apps(slow)
    apps.answers[slow.bundle_id] = AppTimedOut("Slow did not answer within 500 ms")
    clock, memory = ManualClock(), AppMemory()
    first = await reader(apps, memory, clock=clock).read()
    assert first.notes == (COPY.app_hierarchy_unread("Slow", "Slow did not answer within 500 ms"),)
    assert memory.resting == {slow.path: (1, clock.now + wire.SUSPENDED_S)}
    apps.asked.clear()
    assert await reader(apps, memory, clock=clock).read() == type(first)()
    assert apps.asked == []
    clock.advance(wire.SUSPENDED_S)
    await reader(apps, memory, clock=clock).read()
    assert apps.asked == [slow.bundle_id]
    apps.asked.clear()
    del apps.answers[slow.bundle_id]
    apps.listings = [listing("slow", written=5)]
    tree = await reader(apps, memory, clock=clock).read()
    assert apps.asked == [slow.bundle_id] and [node.label for node in tree.walk()] == ["Slow"]
    assert memory.resting == {}
