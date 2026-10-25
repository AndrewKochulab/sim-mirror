# SPDX-License-Identifier: Apache-2.0
"""The Android SDK and its AVDs, read from their files: where they are, what they are, and why one cannot start."""

from __future__ import annotations

from pathlib import Path

from sim_mirror.platform.android_sdk import AndroidSdk, avd_home, avds, find_sdk, read_properties
from sim_mirror.testing.android_sdk import avd, image, sdk_at


def test_the_sdk_is_the_one_named_else_the_environments_else_android_studios(tmp_path: Path) -> None:
    named, env_sdk, studio = tmp_path / "named", tmp_path / "env", tmp_path / "home" / "Library/Android/sdk"
    for folder in (named, env_sdk, studio):
        folder.mkdir(parents=True)
    home = tmp_path / "home"
    assert find_sdk(str(named), {"ANDROID_HOME": str(env_sdk)}, home) == AndroidSdk(named)
    assert find_sdk("", {"ANDROID_HOME": str(env_sdk)}, home) == AndroidSdk(env_sdk)
    assert find_sdk("", {"ANDROID_SDK_ROOT": str(env_sdk)}, home) == AndroidSdk(env_sdk)
    assert find_sdk("", {}, home) == AndroidSdk(studio)
    # A folder that is named but is not there is not replaced by another: the setting says which SDK.
    assert find_sdk(str(tmp_path / "gone"), {}, home) is None
    assert find_sdk("", {}, tmp_path / "nobody") is None


def test_the_tools_and_their_versions_are_read_from_their_packages(tmp_path: Path) -> None:
    sdk = sdk_at(tmp_path / "sdk")
    assert sdk.adb == tmp_path / "sdk/platform-tools/adb" and sdk.emulator == tmp_path / "sdk/emulator/emulator"
    assert (sdk.adb_version, sdk.emulator_version) == ("37.0.0", "36.4.9")
    bare = sdk_at(tmp_path / "bare", adb=False, emulator=False)
    assert (bare.adb_version, bare.emulator_version) == (None, None)


def test_a_properties_file_is_read_by_its_keys_and_an_unreadable_one_is_empty(tmp_path: Path) -> None:
    file = tmp_path / "a.ini"
    file.write_text("# a comment\nkey = value\nempty=\nno separator\npath=/a=b\n")
    assert read_properties(file) == {"key": "value", "empty": "", "path": "/a=b"}
    assert read_properties(tmp_path / "missing.ini") == {}


def test_avds_are_found_where_the_environment_says_else_in_the_home_folder(tmp_path: Path) -> None:
    assert avd_home({"ANDROID_AVD_HOME": "/avds"}, tmp_path) == Path("/avds")
    assert avd_home({"ANDROID_EMULATOR_HOME": "/emu"}, tmp_path) == Path("/emu/avd")
    assert avd_home({}, tmp_path) == tmp_path / ".android" / "avd"


def test_an_avd_says_what_it_is_and_why_it_cannot_start_when_it_cannot(tmp_path: Path) -> None:
    sdk = sdk_at(tmp_path / "sdk")
    home = tmp_path / "avd"
    whole, half = image(sdk, "36"), image(sdk, "36.1", whole=False)
    avd(
        home,
        "Pixel_9",
        "avd.ini.displayname=Pixel 9\nhw.lcd.width=1080\nhw.lcd.height=2424\nhw.lcd.density=420\n"
        f"image.sysdir.1={whole}\n",
    )
    avd(home, "Medium_Phone", f"image.sysdir.1={half}\nhw.lcd.width=wide\n")
    avd(home, "No_Image", "hw.lcd.density=420\n", target="android-34")
    avd(home, "Gone", f"image.sysdir.1={whole}\n")
    (home / "Gone.avd" / "config.ini").unlink()
    (home / "Gone.avd").rmdir()
    found = {made.name: made for made in avds(sdk, {"ANDROID_AVD_HOME": str(home)})}
    assert list(found) == ["Gone", "Medium_Phone", "No_Image", "Pixel_9"]
    pixel = found["Pixel_9"]
    assert (pixel.display_name, pixel.api, pixel.width_px, pixel.height_px, pixel.density, pixel.problem) == (
        "Pixel 9",
        36,
        1080,
        2424,
        420,
        None,
    )
    assert pixel.image == (sdk.root / whole).resolve()
    medium = found["Medium_Phone"]
    assert medium.display_name == "Medium Phone" and medium.width_px is None
    assert medium.problem == (
        "its system image is incomplete (system.img, vendor.img missing): install it again in Android Studio's SDK "
        "Manager"
    )
    assert found["No_Image"].api == 34 and found["No_Image"].problem == (
        "its system image is not installed: install it in Android Studio's SDK Manager"
    )
    assert found["Gone"].problem == f"its folder {home / 'Gone.avd'} is gone"
    without_sdk = avds(None, {"ANDROID_AVD_HOME": str(home)})
    assert {made.problem for made in without_sdk if made.name != "Gone"} == {"no Android SDK was found to run it"}
    assert avds(sdk, {"ANDROID_AVD_HOME": str(tmp_path / "none")}) == []
