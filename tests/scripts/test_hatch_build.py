# SPDX-License-Identifier: Apache-2.0
"""The wheel's build hook: every wheel carries the helper's sources, a release wheel the helper itself."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

import pytest

import hatch_build
from hatch_build import (
    BINARY_ENV,
    BINARY_TARGET,
    MAC_TAG,
    SOURCES_TARGET,
    HelperBinaryMissing,
    WheelHelper,
    apply,
    wheel_helper,
)


def repository(root: Path) -> Path:
    (root / "helper" / "Sources").mkdir(parents=True)
    (root / "helper" / "Tests").mkdir()
    (root / "helper" / "Package.swift").write_text("// swift-tools-version:6.0\n")
    return root


def executable(path: Path, mode: int = 0o755) -> Path:
    path.write_bytes(b"\xca\xfe\xba\xbe")
    path.chmod(mode)
    return path


def test_a_wheel_carries_the_helper_package(tmp_path: Path) -> None:
    root = repository(tmp_path)
    helper = wheel_helper(root, {})
    assert helper == WheelHelper(
        {
            str(root / "helper" / "Package.swift"): f"{SOURCES_TARGET}/Package.swift",
            str(root / "helper" / "Sources"): f"{SOURCES_TARGET}/Sources",
            str(root / "helper" / "Tests"): f"{SOURCES_TARGET}/Tests",
        }
    )
    assert helper.tag is None


def test_what_is_not_there_is_left_out(tmp_path: Path) -> None:
    assert wheel_helper(tmp_path, {BINARY_ENV: "  "}) == WheelHelper({})


def test_a_release_wheel_carries_the_built_helper_and_is_tagged_for_macos(tmp_path: Path) -> None:
    root = repository(tmp_path / "repo")
    binary = executable(tmp_path / "sim-mirror-helper")
    helper = wheel_helper(root, {BINARY_ENV: str(binary)})
    assert helper.force_include[str(binary)] == BINARY_TARGET
    assert len(helper.force_include) == 4
    assert helper.tag == MAC_TAG


@pytest.mark.parametrize("make", [lambda path: path, lambda path: executable(path, 0o644)])
def test_a_helper_that_is_not_an_executable_file_fails_the_build(tmp_path: Path, make: Any) -> None:
    binary = make(tmp_path / "sim-mirror-helper")
    with pytest.raises(HelperBinaryMissing, match="not an executable helper"):
        wheel_helper(tmp_path, {BINARY_ENV: str(binary)})


def test_apply_adds_to_what_hatchling_already_includes() -> None:
    build_data: dict[str, Any] = {"force_include": {"a": "b"}}
    apply(build_data, WheelHelper({"c": "d"}))
    assert build_data == {"force_include": {"a": "b", "c": "d"}}


def test_apply_tags_a_wheel_holding_a_binary_as_platform_specific() -> None:
    build_data: dict[str, Any] = {}
    apply(build_data, WheelHelper({"c": "d"}, MAC_TAG))
    assert build_data == {"force_include": {"c": "d"}, "tag": MAC_TAG, "pure_python": False}


class FakeInterface:
    def __init__(self, root: str, target_name: str) -> None:
        self.root = root
        self.target_name = target_name


INTERFACE = "hatchling.builders.hooks.plugin.interface"


def loaded(interface: type | None, monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """The hook file loaded the way hatchling loads it -- by path, never registered as a module -- with `interface`
    standing in for hatchling's, or with no hatchling at all."""
    for name in ["hatchling", "hatchling.builders", "hatchling.builders.hooks", "hatchling.builders.hooks.plugin"]:
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name) if interface else None)
    stand_in = types.ModuleType(INTERFACE)
    stand_in.BuildHookInterface = interface  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, INTERFACE, stand_in if interface else None)
    spec = importlib.util.spec_from_file_location("hatch_build_by_path", hatch_build.__file__)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def with_hatchling(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    return loaded(FakeInterface, monkeypatch)


def test_the_hook_adds_the_helper_to_a_wheel(
    with_hatchling: types.ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(BINARY_ENV, raising=False)
    root = repository(tmp_path)
    build_data: dict[str, Any] = {}
    with_hatchling.HelperBuildHook(str(root), "wheel").initialize("1.0.0", build_data)
    assert sorted(build_data["force_include"].values()) == [
        f"{SOURCES_TARGET}/Package.swift",
        f"{SOURCES_TARGET}/Sources",
        f"{SOURCES_TARGET}/Tests",
    ]
    assert with_hatchling.HelperBuildHook.PLUGIN_NAME == "custom"


def test_the_hook_leaves_an_sdist_alone(with_hatchling: types.ModuleType, tmp_path: Path) -> None:
    build_data: dict[str, Any] = {}
    with_hatchling.HelperBuildHook(str(repository(tmp_path)), "sdist").initialize("1.0.0", build_data)
    assert build_data == {}


def test_without_hatchling_there_is_no_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    module = loaded(None, monkeypatch)
    assert not hasattr(module, "HelperBuildHook")
    assert module.wheel_helper(Path("/nowhere"), {}) == module.WheelHelper({})
