# SPDX-License-Identifier: Apache-2.0
"""Which simulator a test run's destination names: one, by name or udid, or a refusal listing the ones there are."""

from __future__ import annotations

import pytest

from sim_mirror.build.destination import LISTED_MAX, TEXT_MAX, USAGE, choose_destination
from sim_mirror.build.xcodebuild import BuildRefused
from sim_mirror.protocol import DeviceChoice

AIR = "3AEF58CA-1341-4C5D-A09A-EEB4CCDA8BD5"
AIR_OLD = "9F8B988D-A7DE-4B6E-A9FF-11C77C00F59F"
TWIN = "D36780F8-1C58-4F82-84ED-41B03CDA3C6B"


def choice(udid: str, name: str, runtime: str) -> DeviceChoice:
    return {"udid": udid, "name": name, "runtime": runtime, "state": "Shutdown", "created": False}


CHOICES = [
    choice(AIR_OLD, "iPhone Air", "iOS 18.6"),
    choice(AIR, "iPhone Air", "iOS 26.5"),
    choice(TWIN, "iPhone 17", "iOS 26.5"),
    choice("F77CF46B-B186-4269-B845-131032DB133E", "iPhone 17", "iOS 26.5"),
    choice("2D961125-B1AB-402F-AABA-BE4B53837EF0", "iPad Pro", "iOS 26.5"),
]


def test_a_simulator_is_named_by_its_name_by_its_udid_in_any_case_or_by_its_name_on_a_runtime() -> None:
    assert choose_destination(CHOICES, {"name": "iPad Pro"})["udid"] == "2D961125-B1AB-402F-AABA-BE4B53837EF0"
    assert choose_destination(CHOICES, {"udid": AIR.lower()})["udid"] == AIR
    assert choose_destination(CHOICES, {"name": " iPhone Air ", "runtime": "iOS 18.6"})["udid"] == AIR_OLD
    assert choose_destination(CHOICES, {"udid": AIR, "runtime": "iOS 26.5"})["udid"] == AIR
    # A runtime sent as null is one left out.
    assert choose_destination(CHOICES, {"name": "iPad Pro", "runtime": None})["name"] == "iPad Pro"


def test_a_name_several_simulators_have_asks_for_a_runtime_or_on_one_runtime_for_a_udid() -> None:
    with pytest.raises(
        BuildRefused, match=r"several simulators are named iPhone Air; add a runtime: iOS 18\.6, iOS 26\.5$"
    ):
        choose_destination(CHOICES, {"name": "iPhone Air"})
    with pytest.raises(BuildRefused, match=f"on one runtime; name one by udid: {TWIN} \\(iOS 26\\.5\\), F77CF46B"):
        choose_destination(CHOICES, {"name": "iPhone 17", "runtime": "iOS 26.5"})


def test_a_simulator_that_is_not_there_is_refused_with_the_ones_there_are() -> None:
    listed = (
        "iPhone Air (iOS 18.6), iPhone Air (iOS 26.5), iPhone 17 (iOS 26.5), iPhone 17 (iOS 26.5), iPad Pro (iOS 26.5)"
    )
    with pytest.raises(BuildRefused) as refused:
        choose_destination(CHOICES, {"name": "iPhone Air", "runtime": "iOS 27.0"})
    assert (
        str(refused.value)
        == f"there is no simulator iPhone Air (iOS 27.0) on this Mac for this Xcode; there are {listed}"
    )
    with pytest.raises(
        BuildRefused, match=r"there is no simulator 00000000-0000-0000-0000-000000000000 .* there are none$"
    ):
        choose_destination([], {"udid": "00000000-0000-0000-0000-000000000000"})
    many = [choice(f"{n:08X}-0000-0000-0000-000000000000", f"iPhone {n}", "iOS 26.5") for n in range(LISTED_MAX + 3)]
    with pytest.raises(BuildRefused, match=r"iPhone 19 \(iOS 26\.5\) and 3 more$"):
        choose_destination(many, {"name": "iPhone X"})


@pytest.mark.parametrize(
    "value",
    [
        "iPhone 17",
        {},
        {"runtime": "iOS 26.5"},
        {"name": "iPhone 17", "udid": AIR},
        {"name": "iPhone 17", "os": "26.5"},
        {"name": ""},
        {"name": "   "},
        {"name": 17},
        {"name": "iPhone\n17"},
        {"name": "i" * (TEXT_MAX + 1)},
    ],
)
def test_a_destination_that_is_not_one_says_what_one_is(value: object) -> None:
    with pytest.raises(BuildRefused) as refused:
        choose_destination(CHOICES, value)
    assert str(refused.value) == USAGE


def test_a_udid_that_is_not_one_is_refused_as_such() -> None:
    with pytest.raises(BuildRefused, match=r"'not-a-udid' is not a simulator's udid; destination is"):
        choose_destination(CHOICES, {"udid": "not-a-udid"})
