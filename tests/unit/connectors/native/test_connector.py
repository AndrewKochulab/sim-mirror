# SPDX-License-Identifier: Apache-2.0
"""The native connector: usable where a helper of this version is, full control, and a helper that cannot send input
refused so `auto` goes on."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from sim_mirror._version import __version__
from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import Capability, ConnectorError, ConnectorUnavailable
from sim_mirror.connectors.native import connector as connector_module
from sim_mirror.connectors.native.connector import CAPABILITIES, NativeConnector, create, default_candidates
from sim_mirror.connectors.native.helper import PACKAGED, HelperLauncher, HelperVersion, built_helper
from sim_mirror.connectors.registry import ConnectorContext
from sim_mirror.host_copy import HostCopy
from sim_mirror.platform.simctl import Simctl
from sim_mirror.storage.app_support import STATE_DIR_ENV
from sim_mirror.testing.fakes import BOOTED_UDID, SELECTED_XCODE, FakeEngine, FakeXcodeSelect, MemoryStateStore
from sim_mirror.testing.native import FakeHelper, FakeHelperSpawn, short_run_dir

CONFIG = SimConfig.defaults()
THIS_VERSION = HelperVersion(__version__, 1, "1171.7")


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


class Versions:
    """What each helper says of its version, counting how often it was asked."""

    def __init__(self, answer: HelperVersion | None = THIS_VERSION) -> None:
        self.answer = answer
        self.asked: list[str] = []

    async def __call__(self, binary: str) -> HelperVersion | None:
        self.asked.append(binary)
        return self.answer


@asynccontextmanager
async def native(
    tmp_path: Path,
    *,
    helper: FakeHelper | None = None,
    versions: Versions | None = None,
    candidates: Sequence[Path] | None = None,
) -> AsyncIterator[tuple[NativeConnector, FakeHelperSpawn, Versions]]:
    spawn = FakeHelperSpawn(helper)
    versions = versions or Versions()
    found = [_executable(tmp_path / "bin" / "sim-mirror-helper")] if candidates is None else list(candidates)
    with short_run_dir() as run:
        launcher = HelperLauncher(
            run_dir=run,
            log_dir=tmp_path / "logs",
            owner_tag="SimMirrorTest",
            spawn=spawn,
            signal_group=spawn.signal_group,
            pid_alive=lambda pid: False,
            owner=777,
        )
        yield (
            NativeConnector(
                launcher, copy=HostCopy(), candidates=lambda: found, ask_version=versions, choose=FakeXcodeSelect()
            ),
            spawn,
            versions,
        )


async def test_with_a_helper_of_this_version_it_has_every_capability_but_building(tmp_path: Path) -> None:
    async with native(tmp_path) as (connector, _spawn, versions):
        report = await connector.probe(CONFIG)
        assert report.available and report.name == "native" and report.capabilities == CAPABILITIES
        assert Capability.BUILD_PREVIEW not in CAPABILITIES and Capability.INPUT_TOUCH in CAPABILITIES
        binary = str(tmp_path / "bin" / "sim-mirror-helper")
        assert report.versions == {"sim-mirror-helper": binary, "version": __version__, "CoreSimulator": "1171.7"}
        await connector.probe(CONFIG)
        assert versions.asked == [binary]


async def test_a_helper_that_does_not_say_its_core_simulator_is_still_reported(tmp_path: Path) -> None:
    async with native(tmp_path, versions=Versions(HelperVersion(__version__, 1, None))) as (connector, _spawn, _v):
        assert "CoreSimulator" not in (await connector.probe(CONFIG)).versions


async def test_without_a_helper_it_cannot_be_used_and_says_how_to_build_one(tmp_path: Path) -> None:
    async with native(tmp_path, candidates=[tmp_path / "missing"]) as (connector, spawn, _versions):
        report = await connector.probe(CONFIG)
        assert not report.available and "sim-mirror helper build" in report.reasons[0]
        with pytest.raises(ConnectorUnavailable) as refused:
            await connector.attach(BOOTED_UDID, CONFIG.with_values(native_helper_path="/x/sim-mirror-helper"))
        assert refused.value.status == 409 and "/x/sim-mirror-helper cannot be run" in str(refused.value)
        assert spawn.started == []


@pytest.mark.parametrize(
    ("answer", "says"),
    [
        (None, "a helper that does not say its version"),
        (HelperVersion("0.9.0", 1, None), "version 0.9.0 (wire 1)"),
        (HelperVersion(__version__, 2, None), f"version {__version__} (wire 2)"),
    ],
)
async def test_a_helper_of_another_version_is_not_used(tmp_path: Path, answer: HelperVersion | None, says: str) -> None:
    async with native(tmp_path, versions=Versions(answer)) as (connector, _spawn, _versions):
        report = await connector.probe(CONFIG)
        assert not report.available and says in report.reasons[0]
        assert f"needs version {__version__} (wire 1)" in report.reasons[0]


async def test_attaching_starts_a_helper_that_drives_the_device_and_closing_ends_it(tmp_path: Path) -> None:
    config = CONFIG.with_values(native_hid_transport="indigo", native_idle_key_frames=False, native_startup_timeout=9)
    async with native(tmp_path) as (connector, spawn, _versions):
        session = await connector.attach(BOOTED_UDID, config)
        try:
            argv, _log = spawn.started[0]
            assert argv[argv.index("--hid") + 1] == "indigo" and argv[-1] == "off"
            assert spawn.envs[0] is not None and spawn.envs[0]["DEVELOPER_DIR"] == SELECTED_XCODE
            assert session.connector == "native" and session.capabilities == CAPABILITIES and session.alive
            assert session.screen is session.input is session.reader
            assert (await session.screen.describe()).width_pt == 402
            for _ in range(50):
                if any(request.get("op") == "accessibility" for request in spawn.helper.requests):
                    break
                await asyncio.sleep(0.01)
            assert [request["op"] for request in spawn.helper.requests][:2] == ["describe", "hello"]
            assert {"op": "accessibility"} in spawn.helper.requests
        finally:
            await session.close()
        assert not session.alive
        assert await connector.reap_orphans() == 0


async def test_a_helper_that_cannot_send_input_is_ended_and_refused_saying_why(tmp_path: Path) -> None:
    helper = FakeHelper(hid=None, reasons=["dtuhid: no digitizer", "indigo: no legacy client"])
    async with native(tmp_path, helper=helper) as (connector, spawn, _versions):
        with pytest.raises(ConnectorUnavailable) as refused:
            await connector.attach(BOOTED_UDID, CONFIG)
        assert refused.value.status == 409
        assert str(refused.value) == (
            "The native helper cannot send input to this simulator: dtuhid: no digitizer; indigo: no legacy client"
        )
        assert spawn.processes[0].returncode is not None
    async with native(tmp_path, helper=FakeHelper(hid=None)) as (connector, _spawn, _versions):
        with pytest.raises(ConnectorUnavailable, match="it did not say why"):
            await connector.attach(BOOTED_UDID, CONFIG)


async def test_a_helper_whose_hello_fails_is_ended(tmp_path: Path) -> None:
    helper = FakeHelper()
    async with native(tmp_path, helper=helper) as (connector, spawn, _versions):
        original = helper._hello

        def broken(screen: object) -> dict[str, object]:
            answer = original(screen)
            del answer["wire"]
            return answer

        helper._hello = broken  # type: ignore[method-assign]
        with pytest.raises(ConnectorError, match="hello cannot be read"):
            await connector.attach(BOOTED_UDID, CONFIG)
        assert spawn.processes[0].returncode is not None


async def test_a_first_read_of_the_screen_that_fails_is_no_failure_of_the_session(tmp_path: Path) -> None:
    engine = FakeEngine()
    engine.accessibility_errors = [ConnectorError("the simulator's accessibility is starting")]
    async with native(tmp_path, helper=FakeHelper(engine)) as (connector, _spawn, _versions):
        session = await connector.attach(BOOTED_UDID, CONFIG)
        try:
            for _ in range(50):
                if not engine.accessibility_errors:
                    break
                await asyncio.sleep(0.01)
            assert session.reader is not None and await session.reader.accessibility() == engine.document
        finally:
            await session.close()


def test_a_helper_is_looked_for_in_the_wheel_and_then_where_it_is_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(STATE_DIR_ENV, str(tmp_path))
    assert tuple(default_candidates()) == (PACKAGED, built_helper(tmp_path))
    assert connector_module._modified(str(tmp_path / "missing")) == 0


def test_a_host_gets_one_whose_helpers_live_in_its_folders(tmp_path: Path) -> None:
    state = MemoryStateStore(tmp_path)
    connector = create(ConnectorContext(state=state, copy=HostCopy(), simctl_for=lambda d: Simctl()))
    assert (
        connector.name == "native" and connector._launcher.socket_for(BOOTED_UDID).parent == state.run_dir() / "native"
    )
