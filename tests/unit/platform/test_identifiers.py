# SPDX-License-Identifier: Apache-2.0
"""An identifier's shape says which kind of device it names."""

from __future__ import annotations

import pytest

from sim_mirror.platform.identifiers import is_device_udid, is_simulator_udid, kind_of
from sim_mirror.platform.simctl import is_udid


@pytest.mark.parametrize(
    ("value", "kind"),
    [
        ("D946616B-6E4F-4F5C-8C76-54FAD9B7D702", "simulator"),
        ("d946616b-6e4f-4f5c-8c76-54fad9b7d702", "simulator"),
        ("00008120-0011223344556677", "physical"),
        ("00008150-00998877665544ab", "physical"),
        ("a" * 40, "physical"),
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
