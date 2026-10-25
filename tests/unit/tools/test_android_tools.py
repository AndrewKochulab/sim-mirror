# SPDX-License-Identifier: Apache-2.0
"""The agent tools on an Android device: listed and chosen beside simulators, a phone only while agents may choose
one, an emulator stopped and shut down as a simulator is, a phone never, and an APK installed."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from sim_mirror.core.actions import AgentActions
from sim_mirror.seams import Caller
from sim_mirror.testing.fakes import (
    ANDROID_PHONE_UDID,
    EMULATOR_UDID,
    FULL_CONTROL,
    FakeAndroidBackend,
    FakeConnector,
    no_wait,
)
from sim_mirror.testing.rig import DeviceRig, scope
from sim_mirror.tools.app import installable
from sim_mirror.tools.context import ToolContext
from sim_mirror.tools.registry import ToolRegistry
from sim_mirror.tools.results import Result, ToolRefused

CALLER = Caller(scope("tp-1"), key="agent-1", title="Claude · notes")
REGISTRY = ToolRegistry()


def android_rig(root: Path) -> DeviceRig:
    rig = DeviceRig(
        root,
        androids=FakeAndroidBackend(),
        android=FakeConnector("android", capabilities=FULL_CONTROL, kinds=frozenset({"android"})),
    )
    rig.config.set(android=True, android_phones=True)
    return rig


async def use(rig: DeviceRig, name: str, arguments: dict[str, Any]) -> Result:
    ctx = ToolContext(
        manager=rig.manager,
        actions=AgentActions(rig.manager, rig.config, clock=rig.clock, sleep=no_wait),
        caller=CALLER,
        config=rig.config.get(CALLER.scope),
        copy=rig.copy,
        sleep=no_wait,
    )
    return await REGISTRY.call(name, arguments, dataclasses.replace(ctx))


def said(result: Result) -> str:
    return str(result["content"][0]["text"])


async def test_android_devices_are_listed_and_chosen_beside_simulators(tmp_path: Path) -> None:
    rig = android_rig(tmp_path)
    listed = said(await use(rig, "sim_device", {"action": "list"})).splitlines()
    assert f"Android emulator · Pixel 9 · Android 16 (API 36) · Shutdown · udid {EMULATOR_UDID}" in listed
    assert f"Android phone · Pixel 8 · Android 16 (API 36) · Connected · usb · udid {ANDROID_PHONE_UDID}" in listed
    chosen = said(await use(rig, "sim_device", {"action": "choose", "udid": EMULATOR_UDID}))
    assert chosen.startswith("now using Pixel 9") and chosen.endswith(
        "build with: sim_build_run (Gradle), which installs on this device"
    )


async def test_an_agent_switches_to_a_phone_only_while_android_agents_choose_lets_it(tmp_path: Path) -> None:
    rig = android_rig(tmp_path)
    rig.config.set(android_agents_choose=False)
    listed = said(await use(rig, "sim_device", {"action": "list"}))
    assert listed.endswith(rig.copy.android_agents_may_not_choose())
    refused = await use(rig, "sim_device", {"action": "choose", "udid": ANDROID_PHONE_UDID})
    assert refused["isError"] is True and said(refused) == rig.copy.android_agents_may_not_choose()
    allowed = await use(rig, "sim_device", {"action": "choose", "udid": EMULATOR_UDID})
    assert allowed["isError"] is False


async def test_an_emulator_is_stopped_and_shut_down_as_a_simulator_is_and_a_phone_never(tmp_path: Path) -> None:
    rig = android_rig(tmp_path)
    assert rig.androids is not None
    rig.memory.choose(CALLER.scope, False, EMULATOR_UDID)
    assert said(await use(rig, "sim_device", {"action": "info"})) == (
        "No Android device is running here; sim_device boot starts it."
    )
    await use(rig, "sim_device", {"action": "boot"})
    kept = said(await use(rig, "sim_device", {"action": "stop"}))
    assert "the emulator keeps running, and shutdown: true shuts it down too" in kept
    await use(rig, "sim_device", {"action": "boot"})
    shut = said(await use(rig, "sim_device", {"action": "stop", "shutdown": True}))
    assert "the emulator is shut down" in shut and rig.androids.released[-1] == (EMULATOR_UDID, True)
    await use(rig, "sim_device", {"action": "choose", "udid": ANDROID_PHONE_UDID})
    phone = said(await use(rig, "sim_device", {"action": "stop", "shutdown": True}))
    assert "a real device is never shut down by SimMirror" in phone
    assert rig.androids.released[-1] == (ANDROID_PHONE_UDID, False)


async def test_a_phones_restart_starts_its_session_over_and_an_emulator_is_restarted(tmp_path: Path) -> None:
    rig = android_rig(tmp_path)
    await use(rig, "sim_device", {"action": "choose", "udid": ANDROID_PHONE_UDID})
    assert "the device itself was not restarted" in said(await use(rig, "sim_device", {"action": "restart"}))
    await use(rig, "sim_device", {"action": "choose", "udid": EMULATOR_UDID})
    assert said(await use(rig, "sim_device", {"action": "restart"})).startswith("restarted · Pixel 9")


def test_an_apk_or_a_folder_of_splits_is_installed_on_android_and_an_app_bundle_on_ios(tmp_path: Path) -> None:
    apk = tmp_path / "app-debug.apk"
    apk.write_bytes(b"APK")
    splits = tmp_path / "splits"
    splits.mkdir()
    (splits / "base.apk").write_bytes(b"APK")
    assert installable(str(apk), (tmp_path,), "android") == apk.resolve()
    assert installable(str(splits), (tmp_path,), "android") == splits.resolve()
    with pytest.raises(ToolRefused, match=r"is not an \.apk, or a folder of an app's split APKs"):
        installable(str(tmp_path / "nothing.apk"), (tmp_path,), "android")
    with pytest.raises(ToolRefused, match=r"is not a built \.app"):
        installable(str(apk), (tmp_path,))
    with pytest.raises(ToolRefused, match=r"absolute path of an \.apk"):
        installable("relative.apk", (tmp_path,), "android")
