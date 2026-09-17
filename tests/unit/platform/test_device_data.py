# SPDX-License-Identifier: Apache-2.0
"""A simulator's data folder: worked out, never asked for, and never outside the devices folder."""

from __future__ import annotations

from pathlib import Path

import pytest

from sim_mirror.platform.device_data import DEVICES_DIR_ENV, device_data_dir, devices_dir

UDID = "7A4C5B2E-9E2B-4C43-9F3A-2D0C3F0B6E11"


def test_the_devices_folder_is_xcodes_default_set_unless_the_environment_moves_it(tmp_path: Path) -> None:
    assert devices_dir({}, home=tmp_path) == tmp_path / "Library" / "Developer" / "CoreSimulator" / "Devices"
    assert devices_dir({DEVICES_DIR_ENV: "  "}, home=tmp_path) == devices_dir({}, home=tmp_path)
    assert devices_dir({DEVICES_DIR_ENV: str(tmp_path / "devices")}) == tmp_path / "devices"
    assert devices_dir({DEVICES_DIR_ENV: "~/sims"}) == Path.home() / "sims"
    assert devices_dir({}).name == "Devices"


def test_a_devices_data_folder_is_its_own_under_the_devices_folder(tmp_path: Path) -> None:
    env = {DEVICES_DIR_ENV: str(tmp_path)}
    assert device_data_dir(UDID, env=env) == tmp_path / UDID / "data"
    assert device_data_dir(UDID, env={}, home=tmp_path) == devices_dir({}, home=tmp_path) / UDID / "data"


@pytest.mark.parametrize("udid", ["", "../7A4C5B2E-9E2B-4C43-9F3A-2D0C3F0B6E11", "booted", "7A4C5B2E/../x"])
def test_anything_but_a_device_id_is_refused(udid: str) -> None:
    with pytest.raises(ValueError, match="not a simulator device id"):
        device_data_dir(udid, env={})
