# SPDX-License-Identifier: Apache-2.0
"""One app and one window stay bound across input, discovery, attachment failure and window selection."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from sim_mirror._version import __version__
from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.app.discovery import AppListing
from sim_mirror.connectors.base import Capability, ConnectorError, ConnectorUnavailable, HidEvent
from sim_mirror.connectors.mac import connector as module
from sim_mirror.connectors.mac.connector import CAPABILITIES, INPUT, MacConnector, MacInput, MacWindows, create
from sim_mirror.connectors.native.helper import FoundHelper, HelperVersion
from sim_mirror.connectors.registry import ConnectorContext
from sim_mirror.core.gestures import KEYS
from sim_mirror.host_copy import HostCopy
from sim_mirror.platform.macos import MacApp
from sim_mirror.testing.app_sdk import app_hierarchy, app_node
from sim_mirror.testing.fakes import MemoryStateStore
from sim_mirror.testing.macos import FakeMacOS

APP = MacApp("dev.example.Sample", "Sample", "/Applications/Sample.app", 101)
CONFIG = SimConfig.defaults()
FOUND = FoundHelper("/synthetic/helper", HelperVersion(__version__, 1, None, ("mac-capture",)))
LISTING = AppListing(Path("/synthetic/listing"), 1, 1, "1.0.0", APP.bundle_id, "Sample", 101, 5000, False, "s" * 43)


@pytest.fixture
def rig(monkeypatch):
    fake = FakeMacOS(APP)
    client = SimpleNamespace(
        mac_action=AsyncMock(return_value={"window_id": 7}), accessibility=AsyncMock(return_value={})
    )
    process = SimpleNamespace(engine=client, alive=True)
    launcher = SimpleNamespace(
        start_window=AsyncMock(return_value=process), stop=AsyncMock(), reap_orphans=AsyncMock(return_value=2)
    )
    locate = AsyncMock(return_value=FOUND)
    monkeypatch.setattr(module, "locate_helper", locate)
    monkeypatch.setattr(module, "find_mac_listing", Mock(return_value=None))
    return SimpleNamespace(
        fake=fake,
        client=client,
        process=process,
        launcher=launcher,
        locate=locate,
        connector=MacConnector(launcher, fake.mac),
    )


async def test_probe_requires_opt_in_and_the_window_capture_feature(rig):
    assert not (await rig.connector.probe(CONFIG.with_values(mac_apps=False))).available
    rig.locate.assert_not_awaited()
    for found in (FoundHelper(None), FoundHelper("/helper", HelperVersion(__version__, 1, None, ()))):
        rig.locate.return_value = found
        assert not (await rig.connector.probe(CONFIG)).available
    rig.locate.return_value = FOUND
    assert (await rig.connector.probe(CONFIG)).capabilities == CAPABILITIES | INPUT
    assert (await rig.connector.probe(CONFIG.with_values(mac_accessibility=False))).capabilities == CAPABILITIES


@pytest.mark.parametrize("sdk,ax", [(False, False), (False, True), (True, False), (True, True)])
async def test_attach_binds_the_pid_window_and_permissions_and_close_stops_only_its_helper(rig, monkeypatch, sdk, ax):
    rig.fake.mac.windows[APP.id] = 7
    find = Mock(return_value=LISTING if sdk else None)
    monkeypatch.setattr(module, "find_mac_listing", find)
    session = await rig.connector.attach(APP.id, CONFIG.with_values(mac_accessibility=ax))
    rig.launcher.start_window.assert_awaited_once_with(FOUND.binary, APP.id, 101, window_id=7, accessibility=ax)
    find.assert_called_once_with(Path.home(), APP.bundle_id, 101)
    assert session.screen is rig.client and session.alive and session.buttons == ()
    assert bool(session.reader) == (sdk or ax)
    assert bool(session.input) == bool(session.text) == (sdk or ax)
    assert bool(session.points) == ax
    assert (Capability.INPUT_KEY in session.capabilities) == sdk
    assert session.capabilities == CAPABILITIES | (INPUT if sdk or ax else frozenset()) | (
        {Capability.INPUT_KEY} if sdk else set()
    )
    await session.close()
    rig.launcher.stop.assert_awaited_once_with(rig.process)
    assert await rig.connector.reap_orphans() == 2


async def test_no_helper_or_process_never_starts_and_failed_attach_cleans_up(rig):
    rig.locate.return_value = FoundHelper(None)
    with pytest.raises(ConnectorUnavailable, match="unavailable"):
        await rig.connector.attach(APP.id, CONFIG)
    rig.locate.return_value = FOUND
    rig.fake.mac.known[APP.id] = APP
    rig.fake.apps = [replace(APP, pid=None)]
    with pytest.raises(ConnectorUnavailable, match="not running"):
        await rig.connector.attach(APP.id, CONFIG)
    rig.launcher.start_window.assert_not_awaited()
    rig.fake.apps = [APP]
    rig.client.mac_action.side_effect = ConnectorError("window ended")
    with pytest.raises(ConnectorError, match="ended"):
        await rig.connector.attach(APP.id, CONFIG)
    rig.launcher.stop.assert_awaited_once_with(rig.process)


async def test_window_selection_accepts_only_owned_ids_or_unambiguous_titles(rig):
    windows = MacWindows(rig.client, rig.fake.mac, APP.id)
    rig.client.mac_action.return_value = {
        "windows": [{"id": 7, "title": "Editor"}, {"id": 8, "title": "Editor"}, {"id": 9, "title": "Preview"}]
    }
    for target in (5, "Editor", "Other"):
        with pytest.raises(ConnectorError, match="one window ID"):
            await windows.select_window(target)
        assert APP.id not in rig.fake.mac.windows
    await windows.select_window("Preview")
    assert rig.fake.mac.windows[APP.id] == 9
    await windows.select_window(7)
    assert rig.fake.mac.windows[APP.id] == 7


async def events(*values):
    for value in values:
        yield value


async def test_ax_text_selects_before_setting_and_refuses_submit_before_mutating(rig):
    input = MacInput(rig.client)
    await input.type("Hello")
    await input.mac_action({"kind": "type", "text": "Name", "x": 2, "y": 3})
    assert [call.args[0]["kind"] for call in rig.client.mac_action.await_args_list] == ["type", "tap", "type"]
    rig.client.mac_action.reset_mock()
    with pytest.raises(ConnectorError, match="text was not changed"):
        await input.mac_action({"kind": "type", "text": "Name", "submit": True, "x": 2, "y": 3})
    rig.client.mac_action.assert_not_awaited()
    assert await input.accessibility() == {}


async def test_sdk_uses_the_selected_window_and_validates_the_returned_identity(rig, monkeypatch):
    send = AsyncMock(return_value={})
    document = app_hierarchy(app_node("button", "Save"))
    document["app"].update(bundle_id=APP.bundle_id, pid=101)
    fetch = AsyncMock(return_value=document)
    monkeypatch.setattr(module, "send_input", send)
    monkeypatch.setattr(module, "fetch_hierarchy", fetch)
    input = MacInput(rig.client, LISTING, 7)
    await input.type("Name")
    send.assert_awaited_once_with(LISTING, {"kind": "type", "text": "Name"}, window_id=7, timeout_s=3.0)
    assert (await input.accessibility())["elements"]
    fetch.assert_awaited_once_with(LISTING, max_nodes=3000, timeout_s=3, window_id=7)
    document["app"]["pid"] = 102
    with pytest.raises(ConnectorError):
        await input.accessibility()
    rig.client.accessibility.assert_not_awaited()


async def test_tap_is_one_action_and_drag_or_hardware_buttons_never_become_global_events(rig):
    input = MacInput(rig.client)
    await input.hid(
        events(
            HidEvent.touch("up", 2, 3),
            HidEvent.touch("down", 2, 3),
            HidEvent.touch("down", 2, 3),
            HidEvent.touch("up", 2, 3),
        )
    )
    rig.client.mac_action.assert_awaited_once_with({"kind": "tap", "x": 2, "y": 3})
    for values in [
        (HidEvent.press("home", "down"),),
        (HidEvent.key(4, "down"),),
        (HidEvent.touch("down", 2, 3), HidEvent.touch("down", 3, 3), HidEvent.touch("up", 2, 3)),
        (HidEvent.touch("down", 2, 3), HidEvent.touch("up", 3, 3)),
    ]:
        with pytest.raises(ConnectorError):
            await input.hid(events(*values))
    assert rig.client.mac_action.await_count == 1


async def test_sdk_keys_are_sent_once_and_unknown_sequences_refuse(rig, monkeypatch):
    send = AsyncMock(return_value={})
    monkeypatch.setattr(module, "send_input", send)
    input = MacInput(rig.client, LISTING, 7)
    await input.hid(events(HidEvent.key(KEYS["return"], "down"), HidEvent.key(KEYS["return"], "up")))
    send.assert_awaited_once_with(LISTING, {"kind": "keys", "text": "return"}, window_id=7, timeout_s=3.0)
    with pytest.raises(ConnectorError, match="unsupported"):
        await input.hid(events(HidEvent.key(9999, "down")))


BRIDGE = replace(LISTING, capabilities=frozenset({"trusted_input", "gestures", "keys", "menu"}))


async def test_a_bridge_that_holds_gestures_drags_in_process_and_waits_for_the_gesture(rig, monkeypatch):
    send = AsyncMock(return_value={})
    monkeypatch.setattr(module, "send_input", send)
    input = MacInput(rig.client, BRIDGE, 7)
    assert input.gestures and input.offers("menu") and not MacInput(rig.client, LISTING, 7).gestures
    assert not MacInput(rig.client).gestures
    path = [HidEvent.touch("down", float(x), 5.0) for x in range(100)]
    await input.hid(events(*path, HidEvent.touch("up", 120.0, 5.0)))
    (sent, action), keywords = send.await_args
    assert sent is BRIDGE and action["kind"] == "drag" and action["ms"] == 300 and keywords["timeout_s"] == 3.3
    assert len(action["points"]) == module.DRAG_POINTS
    assert action["points"][0] == [0.0, 5.0] and action["points"][-1] == [120.0, 5.0]
    send.reset_mock()
    await input.mac_action({"kind": "long_press", "x": 1, "y": 2, "ms": 800})
    assert send.await_args.kwargs["timeout_s"] == 3.8
    await input.mac_action({"kind": "tap", "x": 1, "y": 2, "ms": "soon"})
    assert send.await_args.kwargs["timeout_s"] == 3.0


def test_a_long_path_is_thinned_evenly_keeping_its_ends():
    points = [(float(i), 0.0) for i in range(200)]
    kept = module.thinned(points, 5)
    assert kept == [(0.0, 0.0), (50.0, 0.0), (100.0, 0.0), (149.0, 0.0), (199.0, 0.0)]
    assert module.thinned(points[:3], 5) == points[:3]


async def test_a_menu_the_bridge_has_no_hook_for_goes_to_the_menu_bar_through_accessibility(rig, monkeypatch):
    send = AsyncMock(return_value={})
    monkeypatch.setattr(module, "send_input", send)
    menu = {"kind": "menu", "path": ["File", "Save"]}
    unhooked = replace(LISTING, capabilities=frozenset({"gestures"}))
    await MacInput(rig.client, unhooked, 7, ax_menus=True).mac_action(menu)
    rig.client.mac_action.assert_awaited_once_with(menu)
    send.assert_not_awaited()
    for listing, ax in ((unhooked, False), (BRIDGE, True), (LISTING, True)):
        await MacInput(rig.client, listing, 7, ax_menus=ax).mac_action(menu)
    assert send.await_count == 3


def test_the_session_says_what_drives_the_app():
    assert module._note(None).startswith("App-scoped accessibility")
    assert module._note(LISTING) == "App-scoped SimMirrorKit input."
    assert "debug bridge" in module._note(BRIDGE)


def test_factory_reuses_the_runtime_mac_owner(tmp_path):
    fake = FakeMacOS(APP)
    context = ConnectorContext(MemoryStateStore(tmp_path), HostCopy(), lambda _: None, mac=fake.mac)
    assert create(context).mac is fake.mac
    assert isinstance(create(replace(context, mac=None)), MacConnector)


async def test_a_session_ends_once_its_process_does_and_the_device_follows_the_attached_one(rig):
    session = await rig.connector.attach(APP.id, CONFIG)
    assert rig.fake.mac.held[APP.id] == 101 and session.alive
    # The app replaced by a new build: LaunchServices lists another process, and the next call attaches that.
    rig.fake.apps = [replace(APP, pid=202)]
    await rig.fake.mac.apps()
    assert not session.alive
    await rig.connector.attach(APP.id, CONFIG)
    assert rig.fake.mac.held[APP.id] == 202
    assert rig.launcher.start_window.await_args.args == (FOUND.binary, APP.id, 202)
    rig.fake.mac.known.clear()
    assert session.alive is rig.process.alive


async def test_a_chosen_window_that_ended_is_chosen_afresh_and_other_refusals_stand(rig):
    rig.fake.mac.windows[APP.id] = 7
    rig.launcher.start_window.side_effect = [ConnectorUnavailable("no window of this process matches"), rig.process]
    await rig.connector.attach(APP.id, CONFIG)
    assert [call.kwargs["window_id"] for call in rig.launcher.start_window.await_args_list] == [7, None]
    assert APP.id not in rig.fake.mac.windows
    rig.launcher.start_window.reset_mock()
    rig.launcher.start_window.side_effect = ConnectorUnavailable("no window of this process matches")
    with pytest.raises(ConnectorUnavailable, match="no window"):
        await rig.connector.attach(APP.id, CONFIG)
    rig.launcher.start_window.assert_awaited_once()


async def test_an_adapter_says_which_clicks_it_plays_and_accessibility_refuses_them(rig, monkeypatch):
    send = AsyncMock(return_value={})
    monkeypatch.setattr(module, "send_input", send)
    # The CEF adapter has played a click count since it shipped, saying so only as trusted input.
    shipped = MacInput(rig.client, BRIDGE, 7)
    assert shipped.multi_click and not shipped.modifier_input and not shipped.middle_click
    newer = MacInput(rig.client, replace(LISTING, capabilities=frozenset({"clicks", "modifiers", "middle_click"})), 7)
    assert newer.multi_click and newer.modifier_input and newer.middle_click
    accessibility = MacInput(rig.client)
    assert not accessibility.multi_click and not MacInput(rig.client, LISTING, 7).multi_click
    for action in (
        {"kind": "tap", "x": 1, "y": 2, "clicks": 2},
        {"kind": "tap", "x": 1, "y": 2, "modifiers": ["cmd"]},
        {"kind": "middle_click", "x": 1, "y": 2},
    ):
        with pytest.raises(ConnectorError, match="accessibility cannot click twice"):
            await accessibility.mac_action(action)
    rig.client.mac_action.assert_not_awaited()
