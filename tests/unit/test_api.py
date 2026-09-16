# SPDX-License-Identifier: Apache-2.0
"""The surface a host embeds SimMirror through, held to its promise.

`api.py` says everything a host needs is named there and anything else may change between minor versions. That is a
promise about a list, so the list is written down here: a name leaving it is a break a person has to decide on, and a
name arriving is a decision too -- adding one is adding something the project then has to keep.

The second test is the one that caught the omissions: it embeds SimMirror the way a host does, importing nothing but
this surface. A host that has to reach past `api` to do the ordinary thing has found a hole in it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sim_mirror import api

#: Every name `api` offers. Change this list deliberately, with the change to `api.__all__`.
SURFACE = {
    "Admission",
    "Authenticator",
    "Caller",
    "ConfigSource",
    "Confirmations",
    "DeviceMemory",
    "HeldDevice",
    "HostCopy",
    "InvalidScope",
    "JsonDeviceMemory",
    "Person",
    "Policy",
    "Refused",
    "Runtime",
    "Scope",
    "SettingOrigin",
    "SettingsAuthenticator",
    "SettingsEditor",
    "SettingsRefused",
    "SettingsStore",
    "SimConfig",
    "SimulatorUnavailable",
    "StateStore",
    "UsageProbe",
    "claims_dir",
    "create_agent_router",
    "create_http_router",
    "create_settings_router",
    "create_socket_router",
    "relay_command",
}


def test_the_public_surface_is_this_and_every_name_on_it_is_really_there() -> None:
    assert set(api.__all__) == SURFACE
    assert len(api.__all__) == len(SURFACE), "a name is listed twice"
    assert sorted(api.__all__) == list(api.__all__), "kept sorted, so a diff shows what changed"
    for name in SURFACE:
        assert getattr(api, name, None) is not None, name


def test_a_host_can_do_the_ordinary_things_without_reaching_past_it(tmp_path: Path) -> None:
    """Make a scope, be told when one cannot exist, remember a device, and find the claims every host shares."""
    scope = api.Scope(id="ws:alpha:checkout", group="alpha", label="alpha · checkout")
    assert scope.label == "alpha · checkout"
    with pytest.raises(api.InvalidScope):
        api.Scope(id="ws:alpha:check/out", group="alpha", label="odd")

    memory = api.JsonDeviceMemory(tmp_path / "devices.json")
    assert memory.assigned(scope, False) is None
    memory.choose(scope, False, "00000000-1111-2222-3333-444444444444")
    assert memory.assigned(scope, False) == "00000000-1111-2222-3333-444444444444"
    # Read back by a second memory over the same file: this is what surviving a restart means.
    assert api.JsonDeviceMemory(tmp_path / "devices.json").assigned(scope, False) == memory.assigned(scope, False)

    # The folder every host on the Mac shares, so two of them never drive one device.
    assert api.claims_dir({"SIM_MIRROR_CLAIMS_DIR": str(tmp_path / "claims")}) == tmp_path / "claims"
