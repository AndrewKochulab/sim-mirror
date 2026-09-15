# SPDX-License-Identifier: Apache-2.0
"""Device claims: one live process per device, a dead or reused pid's claim taken over, and no lock held across an
await."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from sim_mirror.storage.claims import Claim, Claims, DeviceClaimed
from sim_mirror.testing.fakes import BOOTED_UDID

UDID = BOOTED_UDID


def claims(
    folder: Path,
    *,
    pid: int,
    owner: str = "SimMirror",
    alive: Callable[[int], bool] = lambda pid: True,
    started: dict[int, str | None] | None = None,
) -> Claims:
    times = started if started is not None else {}

    async def start_time(pid: int) -> str | None:
        return times.get(pid, f"started-{pid}")

    return Claims(folder, owner=owner, label="demo", pid=pid, pid_alive=alive, start_time=start_time)


async def test_a_device_is_claimed_once_and_the_second_process_is_told_who_has_it(tmp_path: Path) -> None:
    first = claims(tmp_path, pid=100, owner="SimMirror")
    second = claims(tmp_path, pid=200, owner="host")
    await first.acquire(UDID)
    await first.acquire(UDID)
    assert (tmp_path / f"{UDID}.json").exists()
    with pytest.raises(DeviceClaimed) as refused:
        await second.acquire(UDID)
    assert refused.value.claim == Claim(udid=UDID, owner="SimMirror", pid=100, started="started-100", label="demo")
    assert str(refused.value) == "SimMirror (pid 100) on this Mac is already using this simulator"
    assert await second.holder(UDID) == refused.value.claim and await first.holder(UDID) is None


async def test_only_the_claimer_lets_go(tmp_path: Path) -> None:
    first, second = claims(tmp_path, pid=100), claims(tmp_path, pid=200)
    await first.acquire(UDID)
    assert await second.release(UDID) is False
    assert await first.release(UDID) is True and await first.release(UDID) is False
    await second.acquire(UDID)
    assert await second.holder(UDID) is None and await first.holder(UDID) is not None


async def test_a_dead_process_or_a_reused_pid_does_not_hold_a_device(tmp_path: Path) -> None:
    await claims(tmp_path, pid=100).acquire(UDID)
    await claims(tmp_path, pid=200, alive=lambda pid: pid != 100).acquire(UDID)
    reused = claims(tmp_path, pid=300, started={200: "a later start"})
    assert await reused.holder(UDID) is None
    await reused.acquire(UDID)
    assert Claim.from_json((tmp_path / f"{UDID}.json").read_bytes()) is not None


async def test_a_claim_whose_start_cannot_be_read_holds_while_its_pid_lives(tmp_path: Path) -> None:
    await claims(tmp_path, pid=100, started={100: None}).acquire(UDID)
    stranger = claims(tmp_path, pid=200, started={100: "now unknown"})
    record = Claim.from_json((tmp_path / f"{UDID}.json").read_bytes())
    assert record is not None and record.started is None
    with pytest.raises(DeviceClaimed):
        await stranger.acquire(UDID)
    await claims(tmp_path, pid=300, started={100: "x"}).release(UDID)
    reader = claims(tmp_path, pid=400, started={})
    reader_times: dict[int, str | None] = {100: None}

    async def unknown(pid: int) -> str | None:
        return reader_times.get(pid)

    await claims(tmp_path, pid=500).acquire("OTHER-DEVICE")
    unreadable = Claims(tmp_path, owner="x", pid=600, pid_alive=lambda pid: True, start_time=unknown)
    assert await unreadable.holder("OTHER-DEVICE") is not None
    assert await reader.holder("UNCLAIMED") is None


async def test_pids_that_cannot_be_a_process_hold_nothing_and_broken_files_are_no_claim(tmp_path: Path) -> None:
    (tmp_path / f"{UDID}.json").write_text('{"udid": "x", "owner": "o", "pid": 1, "started": null}')
    assert await claims(tmp_path, pid=200).holder(UDID) is None
    (tmp_path / f"{UDID}.json").write_text("{broken")
    assert await claims(tmp_path, pid=200).holder(UDID) is None
    assert Claim.from_json(b'{"udid": "x"}') is None
    with pytest.raises(ValueError, match="not a device id"):
        await claims(tmp_path, pid=200).acquire("../escape")


async def test_a_claim_that_keeps_changing_under_the_lock_is_refused_with_whoever_has_it_last(tmp_path: Path) -> None:
    path = tmp_path / f"{UDID}.json"
    writes = iter(range(1000, 1010))

    def rival_writes(pid: int) -> bool:
        other = next(writes)
        path.write_bytes(Claim(udid=UDID, owner="rival", pid=other, started=None, label="").to_json())
        return True

    await claims(tmp_path, pid=900).acquire(UDID)
    contested = claims(tmp_path, pid=200, alive=rival_writes)
    with pytest.raises(DeviceClaimed) as refused:
        await contested.acquire(UDID)
    assert refused.value.claim.owner == "rival"
