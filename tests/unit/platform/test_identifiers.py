# SPDX-License-Identifier: Apache-2.0
"""An identifier's shape says which kind of device it names."""

from __future__ import annotations

import pytest

from sim_mirror.platform.identifiers import (
    android_id,
    build_destination,
    is_android_id,
    is_device_udid,
    is_simulator_udid,
    kind_of,
)
from sim_mirror.platform.simctl import is_udid


@pytest.mark.parametrize(
    ("value", "kind"),
    [
        ("D946616B-6E4F-4F5C-8C76-54FAD9B7D702", "simulator"),
        ("d946616b-6e4f-4f5c-8c76-54fad9b7d702", "simulator"),
        ("00008120-0011223344556677", "physical"),
        ("00008150-00998877665544ab", "physical"),
        ("a" * 40, "physical"),
        ("avd-Pixel-9-3fa2c1", "android"),
        ("adb-R58M12ABC-9b1e04", "android"),
        ("avd-Pixel-9-3FA2C1", None),
        ("avd--3fa2c1", None),
        ("emulator-5554", None),
        ("avd-Pixel_9-3fa2c1", None),
        ("000081200011223344556677", None),
        ("00008120-001122334455667", None),
        ("D946616B-6E4F-4F5C-8C76-54FAD9B7D70", None),
        ("-rf", None),
        ("", None),
        (None, None),
        (42, None),
    ],
)
def test_the_shape_of_an_identifier_names_its_kind(value: object, kind: str | None) -> None:
    assert kind_of(value) == kind
    assert is_simulator_udid(value) == (kind == "simulator") == is_udid(value)
    assert is_device_udid(value) == (kind == "physical")
    assert is_android_id(value) == (kind == "android")


@pytest.mark.parametrize(
    ("prefix", "name", "made"),
    [
        ("avd", "Pixel_9", "avd-Pixel-9-"),
        ("avd", "Medium_Phone_API_36.1", "avd-Medium-Phone-API-36-1-"),
        ("adb", "R58M12ABC", "adb-R58M12ABC-"),
        # A Wi-Fi phone's serial, as adb's mDNS names it.
        ("adb", "adb-R58M12ABC-x1y2._adb-tls-connect._tcp", "adb-adb-R58M12ABC-x1y2-adb-tls-connect-tcp-"),
        ("avd", "___", "avd-device-"),
        ("avd", "x" * 80, "avd-" + "x" * 48 + "-"),
    ],
)
def test_an_android_device_is_named_by_its_avd_or_serial_made_to_fit_and_kept_apart(
    prefix: str, name: str, made: str
) -> None:
    named = android_id(prefix, name)  # type: ignore[arg-type]
    assert named.startswith(made) and len(named) == len(made) + 6 and len(named) <= 64
    assert kind_of(named) == "android" and named == android_id(prefix, name)  # type: ignore[arg-type]


def test_names_that_read_alike_stay_apart() -> None:
    assert android_id("avd", "Pixel_9") != android_id("avd", "Pixel-9") != android_id("avd", "Pixel.9")


def test_xcodebuild_is_sent_to_a_simulator_or_a_real_device_by_the_shape_of_its_udid() -> None:
    assert build_destination("D946616B-6E4F-4F5C-8C76-54FAD9B7D702") == (
        "platform=iOS Simulator,id=D946616B-6E4F-4F5C-8C76-54FAD9B7D702"
    )
    assert build_destination("00008120-0011223344556677") == "platform=iOS,id=00008120-0011223344556677"
