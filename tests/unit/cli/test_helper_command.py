# SPDX-License-Identifier: Apache-2.0
"""``sim-mirror helper``: which native helper is used and why not, and building one with Xcode -- without Xcode."""

from __future__ import annotations

import io
import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from sim_mirror._version import __version__
from sim_mirror.cli.context import CliContext
from sim_mirror.cli.main import main
from sim_mirror.connectors.native.helper import PROGRAM, built_helper
from sim_mirror.platform.xcrun import XcrunResult
from sim_mirror.testing.fakes import FakeXcrun

THIS = json.dumps({"version": __version__, "wire": 1, "core_simulator": "1171.7"})
XCODE_27 = "/Applications/Xcode27.app/Contents/Developer"


class Here:
    """A terminal whose helper answers `versions` by path, with the Swift sources at `sources`."""

    def __init__(self, root: Path, *, sources: Path | None = None) -> None:
        self.root = root
        self.versions: dict[str, tuple[int, str]] = {}
        self.xcrun = FakeXcrun()
        self.out, self.err = io.StringIO(), io.StringIO()
        state = root / "state"
        self.built = built_helper(state / "helpers")
        self.installed = state / "helper" / PROGRAM
        (root / "config.toml").write_text(f'[device]\ndeveloper_dir = "{XCODE_27}"\n')
        self.ctx = CliContext(
            env={"SIM_MIRROR_STATE_DIR": str(state), "SIM_MIRROR_CONFIG": str(root / "config.toml")},
            cwd=root,
            stdout=self.out,
            stderr=self.err,
            stdin=io.StringIO(),
            home=root,
            run=self.run,
            xcrun=self.xcrun,
            helper_sources=lambda: sources,
        )

    async def run(self, argv: Sequence[str]) -> tuple[int, str]:
        return self.versions.get(argv[0], (127, "")) if argv[1:] == ["version"] or argv[1:] == ("version",) else (1, "")

    def __call__(self, *argv: str) -> int:
        return main(list(argv), ctx=self.ctx)

    def said(self) -> str:
        return self.out.getvalue()


def executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


def test_status_without_a_helper_says_how_to_build_one(tmp_path: Path) -> None:
    here = Here(tmp_path, sources=tmp_path / "helper")
    assert here("helper") == 1
    said = here.said()
    assert "native helper: none" in said and "usable: no -- SimMirror's native helper is not built" in said
    assert f"built for this version: {here.built} (not built)" in said
    assert f"installed at: {here.installed} (not installed)" in said
    assert f"Swift sources: {tmp_path / 'helper'}" in said


def test_status_names_the_installed_helper_and_its_version_as_text_or_json(tmp_path: Path) -> None:
    here = Here(tmp_path)
    executable(here.built)
    here.versions[str(here.installed)] = (0, THIS)
    assert here("helper", "status") == 0
    said = here.said()
    assert here.installed.read_bytes() == here.built.read_bytes(), "the built helper is installed at the one path"
    assert f"native helper: {here.installed} ({__version__}, wire 1, CoreSimulator 1171.7)" in said
    assert f"installed at: {here.installed} (allow this one in Privacy & Security)" in said
    assert "usable: yes" in said and f"built for this version: {here.built}\n" in said
    assert "Swift sources: not in this install" in said
    here.out.truncate(0)
    here.out.seek(0)
    here.versions[str(here.installed)] = (0, json.dumps({"version": "0.9.0", "wire": 1}))
    assert here("helper", "status", "--json") == 1
    status = json.loads(here.said())
    assert status["installed"] == str(here.installed) and status["path"] == str(here.installed)
    assert status["version"] == "0.9.0" and status["usable"] is False and status["core_simulator"] is None
    assert "is version 0.9.0 (wire 1)" in status["reason"] and status["sources"] is None


def test_build_leaves_a_usable_helper_alone_unless_forced_and_needs_sources(tmp_path: Path) -> None:
    here = Here(tmp_path)
    executable(here.built)
    here.versions[str(here.built)] = (0, THIS)
    assert here("helper", "build") == 0 and "already built" in here.said() and here.xcrun.calls == []
    assert here("helper", "build", "--force") == 1
    assert "has no Swift sources for the native helper" in here.err.getvalue()


def products(folder: Path, *, with_helper: bool = True) -> FakeXcrun:
    def answer(args: tuple[str, ...]) -> XcrunResult:
        if args[-1] == "--show-bin-path":
            return XcrunResult(0, f"{folder}\n", "")
        if with_helper:
            executable(folder / PROGRAM)
        return XcrunResult(0, "Build complete!\n", "")

    return FakeXcrun().on("swift", "build", then=answer)


def test_build_builds_with_the_scopes_xcode_and_puts_the_helper_where_it_is_found(tmp_path: Path) -> None:
    here = Here(tmp_path, sources=tmp_path / "helper")
    here.ctx.xcrun = here.xcrun = products(tmp_path / "Products")
    here.versions[str(here.built)] = (0, THIS)
    assert here("helper", "build") == 0
    assert here.built.is_file() and here.built.stat().st_mode & 0o111
    assert here.xcrun.calls[0].developer_dir == XCODE_27
    assert f"from {tmp_path / 'helper'} with {XCODE_27}" in here.said()
    assert f"built the native helper at {here.built} (CoreSimulator 1171.7)" in here.said()


@pytest.mark.parametrize(
    ("setup", "complaint"),
    [
        ("fails", "did not build: error: no such module"),
        ("empty", "the build finished without a sim-mirror-helper"),
        ("old", "says version 0.9.0 (wire 1)"),
        ("silent", "says nothing readable"),
    ],
)
def test_build_that_does_not_give_a_usable_helper_says_why(tmp_path: Path, setup: str, complaint: str) -> None:
    here = Here(tmp_path, sources=tmp_path / "helper")
    if setup == "fails":
        here.ctx.xcrun = FakeXcrun().on("swift", "build", rc=1, err="error: no such module 'Foo'")
    else:
        here.ctx.xcrun = products(tmp_path / "Products", with_helper=setup != "empty")
    if setup == "old":
        here.versions[str(here.built)] = (0, json.dumps({"version": "0.9.0", "wire": 1}))
    assert here("helper", "build") == 1
    assert complaint in here.err.getvalue()


class Signing:
    """This Mac's signing as the command asks it: set up once, or refused."""

    def __init__(self, *, refuses: bool = False) -> None:
        self.refuses, self.made = refuses, False

    def set_up(self) -> str:
        from sim_mirror.platform.local_signing import SigningError

        if self.refuses:
            raise SigningError("could not trust the certificate for code signing")
        self.made = True
        return "ABC123"

    def signer(self) -> str:
        return "local:ABC123" if self.made else ""

    def sign(self, binary: Path) -> None:
        binary.write_text(binary.read_text() + "# signed\n")


def test_sign_makes_this_macs_certificate_once_and_installs_the_helper_signed_with_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sim_mirror.cli import helper as command

    signing = Signing()
    monkeypatch.setattr(command, "LocalSigning", lambda *_, **__: signing)
    here = Here(tmp_path)
    assert here("helper", "sign") == 0
    assert "signing with ABC123" in here.said() and "no helper to install yet" in here.said()
    executable(here.built)
    assert here("helper", "sign") == 0
    assert f"installed {here.installed}, signed with it" in here.said()
    assert here.installed.read_text().endswith("# signed\n")
    here.versions[str(here.installed)] = (0, THIS)
    here("helper", "status")
    assert "signed by: local:ABC123" in here.said()
    signing.refuses = True
    assert here("helper", "sign") == 1 and "could not trust the certificate" in here.err.getvalue()


def test_status_says_how_to_keep_a_grant_when_the_helper_is_signed_as_shipped(tmp_path: Path) -> None:
    here = Here(tmp_path)
    here("helper", "status")
    assert "signed by: as shipped -- run `sim-mirror helper sign` once" in here.said()
