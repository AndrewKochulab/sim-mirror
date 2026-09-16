# SPDX-License-Identifier: Apache-2.0
"""A host's wording: neutral by default, and a host can say exactly what its own settings page is called."""

from __future__ import annotations

from sim_mirror.host_copy import HostCopy
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
