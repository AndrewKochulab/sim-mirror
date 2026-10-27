# SPDX-License-Identifier: Apache-2.0
"""A host's wording: neutral by default, and a host can say exactly what its own settings page is called."""

from __future__ import annotations

from sim_mirror.host_copy import HostCopy
from sim_mirror.protocol import DeviceKind
from sim_mirror.scope import Scope
from sim_mirror.seams import Admission, Caller, Person, Refused


def test_the_defaults_point_at_the_config_command() -> None:
    copy = HostCopy()
    assert copy.off() == "The iOS Simulator is off for this project (`sim-mirror config`)."
    assert "config set connectors.idb.companion_path" in copy.companion_missing("")
    assert copy.claimed("SimMirror", 42).startswith("Another SimMirror on this Mac (pid 42)")


def test_a_host_supplies_its_own_words() -> None:
    copy = HostCopy(
        scope_noun="workspace",
        settings="Settings → Sessions & Chats",
        simulator_settings="Settings → Sessions & Chats → iOS Simulator",
        area_off="The Terminals area is off for this workspace.",
        companion_path_hint="set its path in Settings",
        owner_name="host",
    )
    assert copy.off() == "The iOS Simulator is off for this workspace (Settings → Sessions & Chats)."
    assert copy.agent_tools_off() == (
        "The iOS Simulator's agent tools are off for this workspace (Settings → Sessions & Chats)."
    )
    assert copy.build_tools_off().startswith("The iOS Simulator's build tools are off for this workspace")
    assert copy.shells_not_allowed() == (
        "A build runs commands, and this workspace does not allow them (Settings → Sessions & Chats)."
    )
    assert copy.companion_missing("") == (
        "The simulator companion (idb_companion) is not installed. "
        "Install it with `brew install facebook/fb/idb-companion`, or set its path in Settings."
    )
    assert copy.companion_missing("/x/idb_companion").startswith("The simulator companion at /x/idb_companion cannot")
    assert copy.too_many_booted(1, 2) == (
        "1 simulator is already running and in use, and this Mac keeps at most 2 booted "
        "(Settings → Sessions & Chats → iOS Simulator)."
    )
    assert copy.too_many_booted(3, 3).startswith("3 simulators are")
    assert copy.no_runtime("iOS 30.0") == (
        "No iOS 30.0 simulator runtime is installed (Settings → Sessions & Chats → iOS Simulator)."
    )
    assert copy.no_runtime("").startswith("No iOS simulator runtime is installed. Add one in Xcode")
    assert copy.claimed("other", 7).endswith("give this host a different device.")


def test_a_real_device_taken_elsewhere_is_called_a_device_and_a_simulator_keeps_its_words() -> None:
    copy = HostCopy(scope_noun="workspace", owner_name="host")
    assert copy.claimed_of_kind("other", 7, "simulator") == copy.claimed("other", 7)
    assert copy.claimed_of_kind("other", 7, "physical") == (
        "Another other on this Mac (pid 7) is already using this device. Stop it there, or give this host a "
        "different device."
    )
    assert copy.in_use_elsewhere_of_kind("simulator") == copy.device_in_use_elsewhere()
    assert copy.in_use_elsewhere_of_kind("physical") == (
        "That device is in use by a workspace of another host on this daemon: choose another device, or a simulator."
    )


def test_a_host_that_rewrote_the_old_words_keeps_them_until_it_rewrites_the_kind_aware_ones() -> None:
    class Rewrote(HostCopy):
        def claimed(self, owner: str, pid: int) -> str:
            return f"{owner} has it"

        def device_in_use_elsewhere(self) -> str:
            return "someone else has it"

    class Both(Rewrote):
        def claimed_of_kind(self, owner: str, pid: int, kind: DeviceKind) -> str:
            return f"{owner} has this {kind}"

        def in_use_elsewhere_of_kind(self, kind: DeviceKind) -> str:
            return f"another has this {kind}"

    assert Rewrote().claimed_of_kind("other", 7, "physical") == "other has it"
    assert Rewrote().in_use_elsewhere_of_kind("physical") == "someone else has it"
    assert Both().claimed_of_kind("other", 7, "physical") == "other has this physical"
    assert Both().in_use_elsewhere_of_kind("physical") == "another has this physical"


def test_who_is_asking_and_a_refusal_carry_what_they_say() -> None:
    scope = Scope.named("demo")
    assert Person(scope).scope is scope
    assert Caller(scope, key="k", title="Codex").title == "Codex"
    assert Admission(scope).scope is scope
    refused = Refused(403, "no")
    assert (refused.status, refused.message, str(refused)) == (403, "no", "no")


def test_reading_through_xcode_says_which_xcode_and_how_to_have_xcode_approve_the_host() -> None:
    copy = HostCopy(simulator_settings="Settings → Simulator", owner_name="Host", xcode_approve_command="host approve")
    assert copy.mcpbridge_missing("") == (
        "Reading the screen through Xcode needs Xcode 27 or later; the Xcode in use is older, or has no mcpbridge "
        "(Settings → Simulator)."
    )
    assert " at /X.app/Contents/Developer is older" in copy.mcpbridge_missing("/X.app/Contents/Developer")
    assert copy.xcode_not_approved() == (
        "Xcode has not approved Host to use its tools yet. Xcode approves an agent that opens a project through them: "
        "run `host approve /path/to/App.xcodeproj` once, and allow it if Xcode asks."
    )


def test_an_apps_shared_hierarchy_says_what_went_wrong_and_where_to_read_about_it() -> None:
    copy = HostCopy(simulator_settings="Settings → Simulator", owner_name="Host", app_sdk_docs="https://host.test/sdk")
    assert copy.app_hierarchy_unread("Notes", "Notes did not answer within 500 ms") == (
        "Notes shares its view hierarchy, but this snapshot could not read it: Notes did not answer within 500 ms"
    )
    assert copy.app_hierarchy_cut("Notes", 3000) == (
        "The view hierarchy Notes shares was cut short: at most 3000 views are read "
        "(connectors.app.max_nodes, Settings → Simulator)"
    )
    assert copy.app_sdk_newer("Notes", 2) == (
        "Notes was built with a newer SimMirror SDK (app SDK protocol 2) than Host reads: "
        "update it to read all it shares"
    )
    assert copy.app_hierarchy_none().endswith("still read well (https://host.test/sdk)")
    assert HostCopy().app_sdk_docs.endswith("/docs/app-sdk.md")


def test_a_device_that_cannot_be_used_says_what_last_kept_webdriveragent_from_driving_it() -> None:
    copy = HostCopy()
    ended = "WebDriverAgent ended as it started (Authentication canceled) (at 18:26:04)"
    assert copy.not_usable_after("Phone B", "Not connected", None) == copy.not_usable("Phone B", "Not connected")
    assert copy.not_usable_after("Phone B", "Not connected", ended) == (
        f"{copy.not_usable('Phone B', 'Not connected')} WebDriverAgent's last error there: {ended}"
    )

    class Rewrote(HostCopy):
        def not_usable(self, name: str, detail: str | None) -> str:
            return f"{name} is busy"

    class Both(Rewrote):
        def not_usable_after(self, name: str, detail: str | None, last_error: str | None) -> str:
            return f"{name} is busy: {last_error}"

    assert Rewrote().not_usable_after("Phone B", None, ended) == "Phone B is busy", "a host's own words are kept"
    assert Both().not_usable_after("Phone B", None, ended) == f"Phone B is busy: {ended}"


def test_webdriveragent_s_words_say_a_cancelled_passcode_prompt_its_end_and_how_long_a_start_may_take() -> None:
    copy = HostCopy()
    cancelled = copy.wda_ended("Underlying Error: Authentication canceled. Canceled by user.", "/logs/wda.log", "U1")
    assert "Enable UI Automation" in cancelled and "a passcode prompt on the device was cancelled" in cancelled
    assert "passcode" not in copy.wda_ended("Testing failed", "/logs/wda.log", "U1")
    assert copy.wda_stopped(9, "Testing started", "/logs/wda.log") == (
        "WebDriverAgent ended on the device (exit 9): Testing started. It starts again when the device is attached "
        "again; `sim_device restart` does that at once. Its log is /logs/wda.log."
    )
    assert copy.wda_stopped(0, "", "/logs/wda.log").startswith("WebDriverAgent ended on the device (exit 0). It")
    assert copy.wda_starting(47, 180).startswith(
        "WebDriverAgent has been starting on the device for 47 s, and is given 180 s"
    )
