# SPDX-License-Identifier: Apache-2.0
"""The commit stamped into a copy of a built simulator app: the app in DerivedData untouched, the copy signed again,
and an app that cannot be stamped installed as it was built."""

from __future__ import annotations

import ctypes
import ctypes.util
import plistlib
from pathlib import Path
from typing import Any

import pytest

from sim_mirror.build import stamp
from sim_mirror.build.source import Source
from sim_mirror.build.stamp import DIRTY_KEY, SHA_KEY, STAMPED, Stamp, StampRefused, compared, stamp_app, stamp_of
from sim_mirror.platform.codesign import RESIGN_TIMEOUT_S, resign_adhoc
from sim_mirror.testing.fakes import FakeXcrun

COMMIT = "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d"
LATER = "9f8e7d6c5b4a39281706f5e4d3c2b1a098f7e6d5"


def built_app(products: Path, *, binary: bool = True, signed: bool = True) -> Path:
    """An app as Xcode leaves it in DerivedData: an Info.plist, an executable, and its signature when it is signed."""
    app = products / "Debug-iphonesimulator" / "Notes.app"
    app.mkdir(parents=True)
    info = {"CFBundleIdentifier": "com.example.Notes", "CFBundleShortVersionString": "1.2"}
    (app / "Info.plist").write_bytes(plistlib.dumps(info, fmt=plistlib.FMT_BINARY if binary else plistlib.FMT_XML))
    (app / "Notes").write_bytes(b"\xcf\xfa\xed\xfe")
    if signed:
        (app / "_CodeSignature").mkdir()
        (app / "_CodeSignature" / "CodeResources").write_bytes(b"<plist/>")
    return app


class Signer:
    def __init__(self, refused: str | None = None) -> None:
        self.signed: list[Path] = []
        self.refused = refused

    async def __call__(self, app: Path) -> str | None:
        self.signed.append(app)
        return self.refused


@pytest.mark.parametrize("binary", [True, False])
async def test_a_copy_holds_the_commit_and_is_signed_again_and_the_built_app_is_left_alone(
    tmp_path: Path, binary: bool
) -> None:
    app = built_app(tmp_path / "Products", binary=binary)
    before = (app / "Info.plist").read_bytes()
    signer = Signer()
    copy = await stamp_app(app, Stamp(COMMIT, dirty=True), signer)
    assert copy == tmp_path / "Products" / STAMPED / "Notes.app"
    written = (copy / "Info.plist").read_bytes()
    assert written.startswith(b"bplist") is binary
    info = plistlib.loads(written)
    assert info[SHA_KEY] == COMMIT and info[DIRTY_KEY] is True and info["CFBundleShortVersionString"] == "1.2"
    assert (copy / "Notes").read_bytes() == b"\xcf\xfa\xed\xfe"
    assert signer.signed == [copy]
    assert (app / "Info.plist").read_bytes() == before
    # A second build's copy replaces the first.
    again = await stamp_app(app, Stamp(LATER), signer)
    assert again == copy and stamp_of(plistlib.loads((again / "Info.plist").read_bytes())) == Stamp(LATER)
    stamp.discard(copy)
    assert not copy.exists()


async def test_an_unsigned_app_is_not_signed_and_a_disk_that_cannot_clone_copies(tmp_path: Path) -> None:
    app = built_app(tmp_path / "Products", signed=False)
    (app / "Frameworks").mkdir()
    (app / "Frameworks" / "Current").symlink_to("A")
    signer = Signer()
    copy = await stamp_app(app, Stamp(COMMIT), signer, clonefile=lambda source, target: False)
    assert signer.signed == []
    assert (copy / "Frameworks" / "Current").is_symlink()
    assert stamp_of(plistlib.loads((copy / "Info.plist").read_bytes())) == Stamp(COMMIT)


async def test_a_linked_info_plist_is_replaced_not_written_through(tmp_path: Path) -> None:
    app = built_app(tmp_path / "Products", signed=False)
    shared = tmp_path / "Shared-Info.plist"
    (app / "Info.plist").rename(shared)
    (app / "Info.plist").symlink_to(shared)
    before = shared.read_bytes()
    copy = await stamp_app(app, Stamp(COMMIT), Signer(), clonefile=lambda source, target: False)
    assert not (copy / "Info.plist").is_symlink() and shared.read_bytes() == before


@pytest.mark.parametrize(
    ("spoil", "said"),
    [
        (lambda app: (app / "Info.plist").unlink(), "Notes.app could not be copied and stamped"),
        (lambda app: (app / "Info.plist").write_bytes(b"not a plist"), "Notes.app could not be copied and stamped"),
        (lambda app: (app / "Info.plist").write_bytes(plistlib.dumps(["a list"])), "is not a dictionary"),
    ],
)
async def test_an_app_that_cannot_be_stamped_leaves_no_copy(tmp_path: Path, spoil: Any, said: str) -> None:
    app = built_app(tmp_path / "Products")
    spoil(app)
    with pytest.raises(StampRefused, match=said):
        await stamp_app(app, Stamp(COMMIT), Signer())
    assert not (tmp_path / "Products" / STAMPED / "Notes.app").exists()


async def test_a_copy_that_cannot_be_signed_again_is_not_kept(tmp_path: Path) -> None:
    app = built_app(tmp_path / "Products")
    with pytest.raises(StampRefused, match="it could not be signed again: the keychain is locked"):
        await stamp_app(app, Stamp(COMMIT), Signer("the keychain is locked"))
    assert not (tmp_path / "Products" / STAMPED / "Notes.app").exists()


def test_a_clone_is_made_in_one_call_where_the_mac_can(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = built_app(tmp_path / "Products")
    assert stamp._clonefile(app, tmp_path / "Clone.app") and (tmp_path / "Clone.app" / "Notes").is_file()
    monkeypatch.setattr(ctypes.util, "find_library", lambda name: None)
    assert not stamp._clonefile(app, tmp_path / "Other.app")
    monkeypatch.setattr(ctypes.util, "find_library", lambda name: "c")
    monkeypatch.setattr(ctypes, "CDLL", lambda name, use_errno: object())
    assert not stamp._clonefile(app, tmp_path / "Other.app")


def test_a_stamp_is_read_only_as_simmirror_writes_one() -> None:
    assert stamp_of({SHA_KEY: COMMIT, DIRTY_KEY: True}) == Stamp(COMMIT, dirty=True)
    assert stamp_of({SHA_KEY: COMMIT, DIRTY_KEY: "yes"}) == Stamp(COMMIT)
    assert stamp_of({SHA_KEY: "1a2b3c4"}) is None and stamp_of({SHA_KEY: 7}) is None and stamp_of({}) is None
    assert stamp.described(Stamp(COMMIT)) == "1a2b3c4"
    assert stamp.described(Stamp(COMMIT, dirty=True)) == "1a2b3c4 with changes not committed"


def test_the_app_is_compared_with_the_folder_as_it_is_now() -> None:
    folder = Path("/Users/dev/Notes")
    line = f"source {folder} · main @ 1a2b3c4"
    assert compared(Stamp(COMMIT), Source(line, COMMIT), folder) == (
        "the code in /Users/dev/Notes: 1a2b3c4, with nothing uncommitted then or now"
    )
    assert compared(Stamp(COMMIT), Source(line, LATER), folder).startswith(
        "not the code in /Users/dev/Notes, which is at 9f8e7d6 now: build again"
    )
    assert "changes not committed when it was built" in compared(
        Stamp(COMMIT, dirty=True), Source(line, COMMIT), folder
    )
    assert "changes not committed in the folder now" in compared(Stamp(COMMIT), Source(line, COMMIT, True), folder)
    assert compared(Stamp(COMMIT), Source(f"source {folder}"), folder) == (
        "/Users/dev/Notes names no commit to compare it with (source /Users/dev/Notes)"
    )


async def test_a_simulator_app_is_signed_again_ad_hoc_keeping_what_xcode_gave_it() -> None:
    xcrun = FakeXcrun()
    assert await resign_adhoc(Path("/dd/Notes.app"), developer_dir="/X.app", xcrun=xcrun) is None
    call = xcrun.calls[-1]
    assert call.args == (
        "codesign", "--force", "--sign", "-", "--preserve-metadata=identifier,entitlements,flags,runtime",
        "--timestamp=none", "/dd/Notes.app",
    )  # fmt: skip
    assert call.timeout == RESIGN_TIMEOUT_S and call.developer_dir == "/X.app"
    xcrun.on("codesign", rc=1, err="Notes.app: resource fork, Finder information, or similar detritus not allowed")
    assert await resign_adhoc(Path("/dd/Notes.app"), xcrun=xcrun) == (
        "Notes.app: resource fork, Finder information, or similar detritus not allowed"
    )
