# SPDX-License-Identifier: Apache-2.0
"""Where an Xcode keeps SimulatorKit -- SharedFrameworks from Xcode 27, PrivateFrameworks before -- and versions."""

from __future__ import annotations

import plistlib
from pathlib import Path

from sim_mirror.connectors.idb import frameworks


def _xcode(tmp_path: Path, *, shared: bool, private: bool) -> Path:
    contents = tmp_path / "Xcode.app" / "Contents"
    (contents / "Developer").mkdir(parents=True)
    if shared:
        (contents / "SharedFrameworks" / "SimulatorKit.framework").mkdir(parents=True)
    if private:
        (contents / "Developer" / "Library" / "PrivateFrameworks" / "SimulatorKit.framework").mkdir(parents=True)
    return contents / "Developer"


def test_xcode_27_keeps_it_in_shared_frameworks_and_that_wins(tmp_path: Path) -> None:
    developer = _xcode(tmp_path, shared=True, private=True)
    found = frameworks.simulator_kit(developer)
    assert found is not None and found.parent.name == "SharedFrameworks"


def test_older_xcodes_keep_it_in_private_frameworks(tmp_path: Path) -> None:
    developer = _xcode(tmp_path, shared=False, private=True)
    found = frameworks.simulator_kit(developer)
    assert found is not None and found.parent.name == "PrivateFrameworks"


def test_an_xcode_without_it_or_the_command_line_tools_have_none(tmp_path: Path) -> None:
    assert frameworks.simulator_kit(_xcode(tmp_path, shared=False, private=False)) is None
    assert frameworks.xcode_contents(Path("/Library/Developer/CommandLineTools")) is None
    assert frameworks.simulator_kit(Path("/Library/Developer/CommandLineTools")) is None


def _plist(path: Path, data: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        plistlib.dump(data, handle)


def test_a_frameworks_version_is_read_from_its_info_plist(tmp_path: Path) -> None:
    flat = tmp_path / "CoreSimulator.framework"
    _plist(flat / "Resources" / "Info.plist", {"CFBundleShortVersionString": "1051.9", "CFBundleVersion": "1051.9.4"})
    assert frameworks.framework_version(flat) == "1051.9 (1051.9.4)"
    versioned = tmp_path / "SimulatorKit.framework"
    (versioned / "Resources").mkdir(parents=True)
    (versioned / "Resources" / "Info.plist").write_text("not a plist")
    _plist(versioned / "Versions" / "A" / "Resources" / "Info.plist", {"CFBundleVersion": "990"})
    assert frameworks.framework_version(versioned) == "990"
    same = tmp_path / "Same.framework"
    _plist(same / "Resources" / "Info.plist", {"CFBundleShortVersionString": "2", "CFBundleVersion": "2"})
    assert frameworks.framework_version(same) == "2"
    empty = tmp_path / "Empty.framework"
    _plist(empty / "Resources" / "Info.plist", {})
    assert frameworks.framework_version(empty) is None
    assert frameworks.framework_version(tmp_path / "Missing.framework") is None
    assert frameworks.CORE_SIMULATOR.name == "CoreSimulator.framework"
