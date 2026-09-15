# SPDX-License-Identifier: Apache-2.0
"""simctl as typed calls: what a Mac lists, a device's life, apps and the screen, and the failures that are the state
asked for."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from sim_mirror.platform.simctl import Simctl, SimctlError, is_udid, runtime_label
from sim_mirror.testing.fakes import BOOTED_UDID, FakeXcrun

UDID = BOOTED_UDID
NEW = "11111111-2222-3333-4444-555555555555"


def make(fake: FakeXcrun | None = None, **kwargs: Any) -> tuple[Simctl, FakeXcrun]:
    fake = fake or FakeXcrun()
    return Simctl(fake, **kwargs), fake


async def test_devices_are_read_from_what_simctl_lists() -> None:
    simctl, fake = make(FakeXcrun().with_lists(), developer_dir="/X.app/Contents/Developer")
    devices = await simctl.devices()
    assert len(devices) == 9 and simctl.developer_dir == "/X.app/Contents/Developer"
    assert [(d.name, d.runtime_id.rsplit(".", 1)[-1]) for d in devices if d.booted] == [("iPhone 17 Pro", "iOS-26-5")]
    found = await simctl.device(UDID)
    assert found is not None and found.available and found.device_type_id.endswith("iPhone-17-Pro")
    assert await simctl.device(NEW) is None
    assert fake.calls[0].args == ("simctl", "list", "devices", "-j")
    assert fake.calls[0].developer_dir == "/X.app/Contents/Developer"


async def test_runtimes_list_their_device_types_newest_first() -> None:
    simctl, _ = make(FakeXcrun().with_lists())
    runtimes = {runtime.name: runtime for runtime in await simctl.runtimes()}
    ios = runtimes["iOS 26.5"]
    assert ios.platform == "iOS" and ios.available and ios.version_key == (26, 5)
    assert [kind.name for kind in ios.device_types][:2] == ["iPhone 17 Pro", "iPhone 17 Pro Max"]
    assert ios.device_types[-1].product_family == "iPad"
    assert runtimes["tvOS 26.5"].device_types == ()


@pytest.mark.parametrize("out", ["not json", "[1, 2]"])
async def test_a_list_that_is_not_an_object_is_an_error(out: str) -> None:
    simctl, _ = make(FakeXcrun().on("simctl", "list", out=out))
    with pytest.raises(SimctlError, match="did not answer"):
        await simctl.devices()


async def test_empty_lists_read_as_nothing() -> None:
    fake = FakeXcrun().on("simctl", "list", "devices", "-j", out='{"devices": {"x": null}}')
    fake.on("simctl", "list", "runtimes", "-j", out='{"runtimes": null}')
    simctl, _ = make(fake)
    assert await simctl.devices() == [] and await simctl.runtimes() == []


async def test_a_failure_carries_what_simctl_said() -> None:
    simctl, _ = make(FakeXcrun().on("simctl", "delete", rc=164, err="An error was encountered\nInvalid device: X\n"))
    with pytest.raises(SimctlError, match="simctl delete: Invalid device: X") as caught:
        await simctl.delete(UDID)
    assert caught.value.result is not None and caught.value.result.rc == 164


async def test_asking_for_the_state_a_device_or_app_is_already_in_succeeds() -> None:
    fake = (
        FakeXcrun()
        .on("simctl", "boot", rc=149, err="Unable to boot device in current state: Booted")
        .on("simctl", "shutdown", rc=149, err="Unable to shutdown device in current state: Shutdown")
        .on("simctl", "terminate", rc=3, err="found nothing to terminate")
    )
    simctl, _ = make(fake)
    await simctl.boot(UDID)
    await simctl.shutdown(UDID)
    assert await simctl.terminate(UDID, "com.acme.Notes") is False
    assert [call.args[1] for call in fake.calls] == ["boot", "shutdown", "terminate"]
    assert (fake.calls[0].timeout, fake.calls[1].timeout) == (120.0, 60.0)


async def test_bootstatus_waits_for_springboard_within_its_timeout() -> None:
    simctl, fake = make()
    await simctl.bootstatus(UDID, timeout=90)
    assert fake.calls[-1].args == ("simctl", "bootstatus", UDID, "-b") and fake.calls[-1].timeout == 90


async def test_create_returns_the_new_devices_id() -> None:
    simctl, fake = make(FakeXcrun().on("simctl", "create", out=NEW + "\n"))
    made = await simctl.create(
        "SimMirror · demo",
        "com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro",
        "com.apple.CoreSimulator.SimRuntime.iOS-26-5",
    )
    assert made == NEW and fake.calls[0].args[1:3] == ("create", "SimMirror · demo")


async def test_a_create_that_answers_no_id_is_an_error() -> None:
    simctl, _ = make(FakeXcrun().on("simctl", "create", out="weird"))
    with pytest.raises(SimctlError, match="no device id"):
        await simctl.create("name", "type", "runtime")


Call = Callable[[Simctl], Awaitable[Any]]


@pytest.mark.parametrize(
    "call",
    [
        lambda s: s.boot("-help"),
        lambda s: s.device("nope"),
        lambda s: s.launch(UDID, "-bad id"),
        lambda s: s.launch(UDID, "com.acme.Notes", ["fine", 3]),
        lambda s: s.openurl(UDID, "--flag"),
        lambda s: s.create("", "type", "runtime"),
        lambda s: s.install(UDID, ""),
        lambda s: s.appearance(UDID, "sepia"),
        lambda s: s.screenshot(UDID, kind="tiff"),
        lambda s: s.log_show(UDID, since_s=5, predicate="-x"),
        lambda s: s.pbcopy(12345, "text"),
    ],
)
async def test_nothing_that_could_read_as_an_option_reaches_simctl(call: Call) -> None:
    simctl, fake = make()
    with pytest.raises(SimctlError):
        await call(simctl)
    assert fake.calls == []


async def test_apps_are_installed_launched_and_opened_and_text_is_pasted() -> None:
    fake = FakeXcrun().on("simctl", "launch", out="com.acme.Notes: 81234\n")
    simctl, _ = make(fake)
    await simctl.install(UDID, "/tmp/Build/Notes.app")
    assert await simctl.launch(UDID, "com.acme.Notes", ["-UITest", "1"]) == 81234
    assert await simctl.launch(UDID, "com.acme.Notes", terminate_running=True) == 81234
    await simctl.openurl(UDID, "notes://new")
    await simctl.pbcopy(UDID, " café 😀")
    await simctl.appearance(UDID, "dark")
    assert fake.argv() == [
        ("simctl", "install", UDID, "/tmp/Build/Notes.app"),
        ("simctl", "launch", UDID, "com.acme.Notes", "-UITest", "1"),
        ("simctl", "launch", "--terminate-running-process", UDID, "com.acme.Notes"),
        ("simctl", "openurl", UDID, "notes://new"),
        ("simctl", "pbcopy", UDID),
        ("simctl", "ui", UDID, "appearance", "dark"),
    ]
    assert fake.calls[0].timeout == 300.0 and fake.calls[4].input_data == " café 😀".encode()


async def test_a_launch_that_prints_no_pid_still_launches() -> None:
    simctl, _ = make(FakeXcrun().on("simctl", "launch", out="launched\n"))
    assert await simctl.launch(UDID, "com.acme.Notes") is None


async def test_a_screenshot_is_the_image_simctl_writes_and_an_empty_one_is_an_error() -> None:
    simctl, fake = make(FakeXcrun().on("simctl", "io", raw=b"\xff\xd8jpeg\xff\xd9"))
    assert await simctl.screenshot(UDID) == b"\xff\xd8jpeg\xff\xd9"
    assert fake.calls[0].args == ("simctl", "io", UDID, "screenshot", "--type=jpeg", "-")
    assert await simctl.screenshot(UDID, kind="png") and fake.calls[1].args[4] == "--type=png"
    empty, _ = make(FakeXcrun().on("simctl", "io", raw=b""))
    with pytest.raises(SimctlError, match="wrote no image"):
        await empty.screenshot(UDID)


async def test_the_log_is_read_compact_for_a_window_and_a_predicate() -> None:
    simctl, fake = make(FakeXcrun().on("simctl", "spawn", out="line\n"))
    assert await simctl.log_show(UDID, since_s=60, predicate='process == "Notes"') == "line\n"
    assert fake.calls[0].args == (
        "simctl", "spawn", UDID, "log", "show", "--last", "60s", "--style", "compact", "--predicate",
        'process == "Notes"',
    )  # fmt: skip


def test_a_runtime_id_reads_as_a_person_says_it_and_a_udid_is_recognised() -> None:
    assert runtime_label("com.apple.CoreSimulator.SimRuntime.iOS-26-5") == "iOS 26.5"
    assert runtime_label("com.apple.CoreSimulator.SimRuntime.xrOS-2-0") == "xrOS 2.0"
    assert runtime_label("custom") == "custom"
    assert is_udid(UDID) and not is_udid("nope") and not is_udid(3)
