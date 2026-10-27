# SPDX-License-Identifier: Apache-2.0
"""A real device's lifecycle on a cabled phone: never run by CI or `make test`, only by `make live-device`.

    SIM_MIRROR_LIVE_DEVICE_UDID=<a cabled, unlocked phone's UDID> make live-device

`tests/contract/test_physical_embedding.py` checks this lifecycle over fake devicectl; this runs the same checks on
the phone itself, through `sim_mirror.api` as a host drives it:

* **reconnect** -- `sim_device restart` starts the session over and answers `reconnected`, the phone still the one in
  use and ready;
* **unplug and replug** -- it asks you to unplug the cable, then that a restart and a boot are refused saying the
  phone is not connected, with no simulator brought up in its place; then to plug it back in, and that it comes back;
* **a cable out while Wi-Fi still reaches the phone** -- it asks you to unplug the cable only, then that the session
  started over is reached without it, saying so in its note; then to plug it back in, and that the session is
  attached again by itself, its cable's log with it, with no tool called;
* **restore after a crash** -- it turns the phone light or dark, abandons the runtime as a crash would, and checks
  that the next runtime's start puts the phone back as it was.

Nothing it runs may ask a phone to reboot, restart, shut down, be erased, unpaired or have anything deleted or
uninstalled: every xcrun call that is devicectl's or names the phone is checked before it is run, refused when it
would, and the test fails. It runs view only -- WebDriverAgent off, and the phone not touchable -- unless
`SIM_MIRROR_LIVE_DEVICE_WDA=1`, which needs WebDriverAgent set up first with `sim-mirror wda setup --device <udid>`
and its team in `SIM_MIRROR_LIVE_DEVICE_TEAM`, and fails unless the phone can then be touched; `sim_act` is listed
either way, and nothing is tapped. `SIM_MIRROR_LIVE_DEVICE_DEVELOPER_DIR` names the Xcode (default: the one
xcode-select names), `SIM_MIRROR_LIVE_HELPER` the native helper that reads the cable screen (default: none, so
screenshots), and `SIM_MIRROR_LIVE_DEVICE_UNATTENDED=1` skips the steps that need a person at the terminal.

`SIM_MIRROR_LIVE_DEVICE_ASK_DIR` names a folder to ask the person through instead of the terminal, for a run nobody
types into: each step is written to `asked` there, and the run waits up to half an hour for an `answered` file.

It uses this Mac's own claims folder, so a SimMirror already driving the phone is respected, and the phone refused,
rather than fought over: stop it first. Without `SIM_MIRROR_LIVE_DEVICE_UDID`, every test is skipped -- that is the dry
run -- and `make test` skips it even with the UDID set.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from sim_mirror import api
from sim_mirror.connectors.base import Capability
from sim_mirror.platform.usbmux import USBMUXD_ENV
from sim_mirror.platform.xcrun import XcrunResult, run_xcrun
from sim_mirror.storage.app_support import STATE_DIR_ENV
from sim_mirror.testing.fakes import FakePolicy, MemoryStateStore, StaticConfig
from sim_mirror.testing.native import short_run_dir
from sim_mirror.testing.vision import FakeTextRecognizer

UDID = os.environ.get("SIM_MIRROR_LIVE_DEVICE_UDID", "")
DEVELOPER_DIR = os.environ.get("SIM_MIRROR_LIVE_DEVICE_DEVELOPER_DIR", "")
HELPER = os.environ.get("SIM_MIRROR_LIVE_HELPER", "")
WDA = os.environ.get("SIM_MIRROR_LIVE_DEVICE_WDA", "") == "1"
UNATTENDED = os.environ.get("SIM_MIRROR_LIVE_DEVICE_UNATTENDED", "") == "1"
TEAM = os.environ.get("SIM_MIRROR_LIVE_DEVICE_TEAM", "")
ASK_DIR = os.environ.get("SIM_MIRROR_LIVE_DEVICE_ASK_DIR", "")
#: How long a person asked through `ASK_DIR` has to answer.
ANSWER_S = 1800.0
#: How long a phone plugged back in may take to be attached again by itself: the cable switching over, then the next
#: look for it, then the attach.
CABLE_BACK_S = 45.0
#: Read before the test's own folders replace the Mac's: the claims every SimMirror on this Mac shares.
CLAIMS = api.claims_dir(dict(os.environ))
#: Read before each test's folders move too: this Mac's usbmuxd, without which no phone is cabled -- no cable screen,
#: log or WebDriverAgent -- and the state folder `sim-mirror wda setup` built WebDriverAgent in.
MACS = {name: os.environ.get(name) for name in (USBMUXD_ENV, STATE_DIR_ENV)}
#: What a lifecycle must never ask devicectl, or anything, of a phone.
NEVER = frozenset({"reboot", "restart", "shutdown", "erase", "uninstall", "unpair", "delete"})
#: How long a phone has to come up: the first time, its cable takes seconds to switch over.
BOOT_TRIES = 6
SCOPE = api.Scope(id="live-device", group="live", label="make live-device")
CALLER = api.Caller(SCOPE, key="live-device", title="make live-device")

pytestmark = [
    pytest.mark.live_device,
    pytest.mark.allow_subprocess,
    pytest.mark.skipif(not UDID, reason="SIM_MIRROR_LIVE_DEVICE_UDID names no cabled phone"),
]


@pytest.fixture(autouse=True)
def _the_macs_cable_and_webdriveragent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Undo what every test is given -- a usbmuxd socket and a state folder of its own, with nothing in them -- for
    the two this run needs from the Mac."""
    for name, value in MACS.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)


@pytest.fixture(autouse=True)
def _only_from_make_live_device(request: pytest.FixtureRequest) -> None:
    """Skipped unless picked by its marker, as `make live-device` picks it: a UDID left in the environment never makes
    `make test` drive the phone."""
    if "live_device" not in str(request.config.option.markexpr or ""):
        pytest.skip("runs only from make live-device (pytest -m live_device)")


def refused_argv(args: tuple[str, ...]) -> bool:
    """Whether an xcrun call would ask something of the phone that SimMirror never asks: only devicectl's, and those
    that name the phone, are looked at -- simctl shutting down a simulator is no concern here."""
    about_the_phone = (args[:1] == ("devicectl",)) or UDID in args
    return about_the_phone and bool(NEVER & set(args))


class Guarded:
    """The real xcrun, every call written down, and one that would reboot, shut down or erase the phone refused before
    it is run."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.refused: list[tuple[str, ...]] = []

    async def __call__(
        self,
        *args: str,
        timeout: float = 30.0,
        developer_dir: str = "",
        input_data: bytes | None = None,
        cwd: Path | str | None = None,
    ) -> XcrunResult:
        self.calls.append(args)
        if refused_argv(args):
            self.refused.append(args)
            return XcrunResult(1, "", "refused by make live-device: SimMirror never asks this of a phone")
        return await run_xcrun(*args, timeout=timeout, developer_dir=developer_dir, input_data=input_data, cwd=cwd)


class Store(MemoryStateStore):
    """A test's own folders, but this Mac's claims: a SimMirror already driving the phone keeps it."""

    def claims_dir(self) -> Path:
        return CLAIMS


def ask(what: str) -> None:
    """Have the person at the terminal, or answering through `ASK_DIR`, do something to the phone, and wait for them."""
    if ASK_DIR:
        asked_through(Path(ASK_DIR), what)
        return
    if UNATTENDED or not sys.stdin.isatty():
        pytest.skip("needs a person at the terminal: run make live-device without SIM_MIRROR_LIVE_DEVICE_UNATTENDED")
    input(f"\n\n>>> {what}, then press Return. ")


async def person(what: str) -> None:
    """`ask`, on a thread: the runtime goes on while the person does it -- noticing a cable come out or go back in --
    as it does while a person handles a phone it drives."""
    await asyncio.to_thread(ask, what)


def asked_through(folder: Path, what: str) -> None:
    """Write what to do to `asked`, and wait for `answered`: both gone again once it comes."""
    asked, answered = folder / "asked", folder / "answered"
    answered.unlink(missing_ok=True)
    asked.write_text(what + "\n", encoding="utf-8")
    print(f"\n>>> {what}: waiting for {answered}", flush=True)
    deadline = time.monotonic() + ANSWER_S
    while not answered.exists():
        if time.monotonic() >= deadline:
            asked.unlink(missing_ok=True)
            pytest.fail(f"nobody answered in {ANSWER_S:.0f} s: {what}")
        time.sleep(1.0)
    answered.unlink(missing_ok=True)
    asked.unlink(missing_ok=True)


class Phone:
    """A host's runtime over the cabled phone, as `sim_mirror.api` builds it, with every xcrun call guarded."""

    def __init__(self, root: Path) -> None:
        self.xcrun = Guarded()
        self.state = Store(root)
        self.config = StaticConfig(
            api.SimConfig.defaults().with_values(
                real_devices=True,
                real_devices_agents_choose=True,
                wda_enabled=WDA,
                real_devices_team_id=TEAM,
                developer_dir=DEVELOPER_DIR,
                native_helper_path=HELPER,
            )
        )

    def build(self) -> api.Runtime:
        return api.Runtime.build(
            config=self.config,
            state=self.state,
            policy=FakePolicy(),
            memory=api.JsonDeviceMemory(self.state.devices_file()),
            xcrun=self.xcrun,
            vision=FakeTextRecognizer(),
        )

    @asynccontextmanager
    async def running(self) -> AsyncIterator[api.Runtime]:
        runtime = self.build()
        await runtime.start()
        try:
            yield runtime
        finally:
            await runtime.close()

    def asked_of_the_phone(self) -> list[tuple[str, ...]]:
        return [args for args in self.xcrun.calls if args[:1] == ("devicectl",) or UDID in args]


async def call(runtime: api.Runtime, name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
    answer = await runtime.call(CALLER, name, arguments)
    return bool(answer.get("isError")), str(answer["content"][0]["text"])


async def boot(runtime: api.Runtime) -> str:
    """The phone's session opened, however long its cable takes to switch over."""
    said = ""
    for _ in range(BOOT_TRIES):
        refused, said = await call(runtime, "sim_device", {"action": "boot"})
        if not refused:
            return said
        if "call again shortly" not in said:
            break
    raise AssertionError(f"the phone did not come up: {said}")


async def picked(runtime: api.Runtime) -> str:
    """Choose the phone for the scope, and open its session: driven by WebDriverAgent when the run asks for it, else
    view only."""
    refused, said = await call(runtime, "sim_device", {"action": "choose", "udid": UDID})
    assert not refused, said
    said = await boot(runtime)
    offered = [tool["name"] for tool in (await runtime.manifest(SCOPE))["tools"]]
    instance = runtime.manager.instance(SCOPE)
    why = instance.fallback_reason if instance is not None else "no instance"
    assert "sim_act" in offered, f"listed whatever the phone can do: {offered}"
    touchable = instance is not None and Capability.INPUT_TOUCH in instance.capabilities
    assert touchable == WDA, f"WebDriverAgent {'on' if WDA else 'off'}, touchable: {touchable}; {why}"
    return said


@pytest.fixture
def phone() -> Iterator[Phone]:
    with short_run_dir() as root:
        here = Phone(root)
        yield here
        assert here.xcrun.refused == [], f"asked of the phone, and refused: {here.xcrun.refused}"
        asked = here.asked_of_the_phone()
        assert not [args for args in asked if NEVER & set(args)], "a phone is never rebooted, shut down or erased"


async def test_restart_reconnects_the_phone_and_never_restarts_it(phone: Phone) -> None:
    async with phone.running() as runtime:
        assert UDID in await picked(runtime)
        refused, said = await call(runtime, "sim_device", {"action": "restart"})
        assert not refused and said.startswith("reconnected: its session was started over"), said
        assert "not restarted" in said and UDID in said
        refused, said = await call(runtime, "sim_device", {"action": "info"})
        assert not refused and UDID in said and "ready" in said, said
        refused, said = await call(runtime, "sim_device", {"action": "stop", "shutdown": True})
        assert not refused and "a real device is never shut down by SimMirror" in said, said


async def test_an_unplugged_phone_is_refused_saying_why_never_replaced_and_comes_back_once_plugged_in(
    phone: Phone,
) -> None:
    async with phone.running() as runtime:
        await picked(runtime)
        await person("Unplug the phone's cable now and take it off the network: Airplane Mode on")
        refused, said = await call(runtime, "sim_device", {"action": "restart"})
        assert refused and said.startswith("could not reconnect to the device"), said
        refused, said = await call(runtime, "sim_device", {"action": "boot"})
        assert refused and "not connected" in said, said
        assert not [args for args in phone.xcrun.calls if args[:2] == ("simctl", "boot")], "no simulator in its place"
        await person("Plug the cable back in, turn Airplane Mode off and unlock the phone")
        assert UDID in await boot(runtime)


async def test_a_phone_whose_cable_comes_out_while_wi_fi_reaches_it_is_attached_again_once_it_is_back(
    phone: Phone,
) -> None:
    async with phone.running() as runtime:
        await picked(runtime)
        instance = runtime.manager.instance(SCOPE)
        assert instance is not None and Capability.LOGS in instance.capabilities, "its log comes over the cable"
        await person("Unplug the phone's cable now, leaving it unlocked and on the same Wi-Fi as this Mac")
        refused, said = await call(runtime, "sim_device", {"action": "restart"})
        if refused:
            pytest.skip(f"the phone is not reached over Wi-Fi with its cable out: {said}")
        assert Capability.LOGS not in instance.capabilities and "\nnote: " in said, said
        await person("Plug the cable back in and unlock the phone -- nothing else")
        deadline = time.monotonic() + CABLE_BACK_S
        while Capability.LOGS not in instance.capabilities:
            assert time.monotonic() < deadline, f"not attached again by itself: {instance.fallback_reason}"
            await asyncio.sleep(0.5)
        refused, said = await call(runtime, "sim_device", {"action": "info"})
        assert not refused and "ready" in said, said
        refused, said = await call(runtime, "sim_device", {"action": "health"})
        assert not refused and "\nscreen: yes" in said and ("\ntouch: yes" in said) == WDA, said


async def test_what_was_changed_on_the_phone_is_put_back_by_the_next_start_after_a_crash(phone: Phone) -> None:
    crashed = phone.build()
    await crashed.start()
    try:
        await picked(crashed)
        instance = crashed.manager.instance(SCOPE)
        assert instance is not None
        before = (await crashed.manager.control(instance).display(UDID)).appearance
        assert before in ("light", "dark"), f"the phone did not say whether it is light or dark: {before}"
        other = "dark" if before == "light" else "light"
        refused, said = await call(crashed, "sim_device", {"action": "appearance", "mode": other})
        assert not refused, said
        assert (await crashed.manager.control(instance).display(UDID)).appearance == other
        # The crash: nothing is let go of, nothing put back. The next runtime's start finds it written down.
        async with phone.running() as after:
            back = (await after.manager.control(instance).display(UDID)).appearance
            assert back == before, f"the next start left the phone {back}; it was {before}"
    finally:
        await crashed.close()
