# SPDX-License-Identifier: Apache-2.0
"""Which Xcode a child runs with -- the setting, an inherited DEVELOPER_DIR, else xcode-select -- and how it is told."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from sim_mirror.platform.developer_dir import (
    DEVELOPER_DIR,
    ChosenXcode,
    choose_xcode,
    developer_env,
    selected_developer_dir,
)

XCODE_26 = "/Applications/Xcode.app/Contents/Developer"
XCODE_27 = "/Applications/Xcode27.app/Contents/Developer"
WITH_SPACE = "/Applications/Xcode 27 beta.app/Contents/Developer"


class Selects:
    """A stand-in for ``xcode-select -p`` that remembers being asked."""

    def __init__(self, code: int = 0, out: str = f"{XCODE_27}\n") -> None:
        self.code, self.out = code, out
        self.asked: list[tuple[str, ...]] = []

    async def __call__(self, argv: Sequence[str]) -> tuple[int, str]:
        self.asked.append(tuple(argv))
        return self.code, self.out


async def test_the_selected_xcode_is_the_folder_xcode_select_names() -> None:
    selected = Selects()
    assert await selected_developer_dir(selected) == XCODE_27 and selected.asked == [("xcode-select", "-p")]
    unselected = Selects(2, "xcode-select: error: unable to get active developer directory")
    assert await selected_developer_dir(unselected) is None
    assert await selected_developer_dir(Selects(0, "  \n")) is None


async def test_the_setting_wins_over_an_inherited_developer_dir_and_over_xcode_select() -> None:
    selected = Selects()
    chosen = await choose_xcode(XCODE_26, {DEVELOPER_DIR: XCODE_27}, selected)
    assert chosen == ChosenXcode(XCODE_26, "setting") and selected.asked == []


async def test_an_inherited_developer_dir_wins_over_xcode_select_and_a_blank_one_does_not() -> None:
    selected = Selects(out=f"{XCODE_26}\n")
    assert await choose_xcode("", {DEVELOPER_DIR: XCODE_27}, selected) == ChosenXcode(XCODE_27, "environment")
    assert selected.asked == []
    assert await choose_xcode("", {DEVELOPER_DIR: "  "}, selected) == ChosenXcode(XCODE_26, "xcode-select")


async def test_nothing_is_chosen_when_nothing_names_an_xcode() -> None:
    assert await choose_xcode("", {}, Selects(2, "")) is None


async def test_the_process_environment_is_read_when_none_is_given(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DEVELOPER_DIR, XCODE_27)
    assert await choose_xcode("", run=Selects(2, "")) == ChosenXcode(XCODE_27, "environment")


def test_a_chosen_xcode_says_where_it_came_from() -> None:
    assert str(ChosenXcode(XCODE_26, "setting")) == f"{XCODE_26} (device.developer_dir)"
    assert str(ChosenXcode(XCODE_27, "environment")) == f"{XCODE_27} (DEVELOPER_DIR)"
    assert str(ChosenXcode(WITH_SPACE, "xcode-select")) == f"{WITH_SPACE} (xcode-select)"


def test_the_environment_names_an_xcode_only_when_one_is_given(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DEVELOPER_DIR, raising=False)
    monkeypatch.setenv("SIM_MIRROR_KEPT", "yes")
    inherited = developer_env("")
    assert DEVELOPER_DIR not in inherited and inherited["SIM_MIRROR_KEPT"] == "yes"
    assert developer_env(WITH_SPACE)[DEVELOPER_DIR] == WITH_SPACE


def test_a_given_base_is_copied_and_never_changed() -> None:
    base = {DEVELOPER_DIR: XCODE_26, "PATH": "/usr/bin"}
    env = developer_env(XCODE_27, base)
    assert env == {DEVELOPER_DIR: XCODE_27, "PATH": "/usr/bin"} and base[DEVELOPER_DIR] == XCODE_26
    assert developer_env("", base) == base
