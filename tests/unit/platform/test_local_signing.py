# SPDX-License-Identifier: Apache-2.0
"""This Mac's own helper-signing identity: made once, trusted by the person once, used for every helper after."""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Sequence
from pathlib import Path

import pytest

from sim_mirror.platform import local_signing
from sim_mirror.platform.local_signing import (
    CERTIFICATE,
    CODESIGN,
    IDENTITY_ENV,
    KEYCHAIN,
    OPENSSL,
    SECURITY,
    LocalSigning,
    SigningError,
    run_program,
)

DER = b"a made-up certificate"
PEM = "-----BEGIN CERTIFICATE-----\n" + base64.b64encode(DER).decode() + "\n-----END CERTIFICATE-----\n"
FINGERPRINT = hashlib.sha1(DER).hexdigest().upper()


class Programs:
    """openssl, security and codesign as they answer; each argv kept. `trusted` is what find-identity lists."""

    def __init__(self, *, refuse: str = "", trusted: bool = True) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.refuse, self.trusted, self.trusts, self.no_list = refuse, trusted, trusted, False

    def __call__(self, argv: Sequence[str], timeout: float) -> tuple[int, str]:
        call = tuple(argv)
        self.calls.append(call)
        if self.refuse and self.refuse in call:
            return 1, f"{self.refuse} refused"
        if call[:2] == (OPENSSL, "req"):
            Path(call[call.index("-out") + 1]).write_text(PEM)
            Path(call[call.index("-keyout") + 1]).write_text("key")
        if call[:2] == (SECURITY, "add-trusted-cert") and self.trusts:
            self.trusted = True
        if call[:2] == (SECURITY, "create-keychain"):
            Path(call[-1]).write_text("keychain")
        if call[:2] == (SECURITY, "list-keychains") and len(call) == 4:
            return (1, "no list") if self.no_list else (0, '    "/Users/a/Library/Keychains/login.keychain-db"\n')
        if call[:2] == (SECURITY, "find-identity"):
            return 0, f'  1) {FINGERPRINT} "SimMirror"' if self.trusted else "0 valid identities found"
        return 0, ""

    def named(self, word: str) -> list[tuple[str, ...]]:
        return [call for call in self.calls if word in call[:2]]


def test_setting_up_makes_a_certificate_in_a_keychain_of_its_own_and_asks_the_person_once_to_trust_it(
    tmp_path: Path,
) -> None:
    programs = Programs()
    signing = LocalSigning(tmp_path / "signing", run=programs, env={})
    assert not signing.ready() and signing.signer() == ""
    assert signing.set_up() == FINGERPRINT
    assert signing.signer() == f"local:{FINGERPRINT}" and signing.ready()
    trusted = programs.named("add-trusted-cert")
    assert len(trusted) == 1 and "codeSign" in trusted[0] and str(signing.keychain) in trusted[0]
    assert not any("login.keychain" in part for call in programs.calls for part in call), "never the login keychain"
    assert sorted(path.name for path in signing.folder.iterdir()) == [CERTIFICATE, "keychain-password", KEYCHAIN]
    assert oct((signing.folder / "keychain-password").stat().st_mode & 0o777) == "0o600"
    asked = len(programs.calls)
    assert signing.set_up() == FINGERPRINT and not programs.named("add-trusted-cert")[1:], "set up once"
    assert len(programs.calls) > asked


def test_a_certificate_the_person_did_not_trust_is_thrown_away_so_helpers_stay_as_shipped(tmp_path: Path) -> None:
    for programs in (Programs(refuse="add-trusted-cert"), Programs(trusted=False)):
        signing = LocalSigning(tmp_path / str(len(programs.calls)) / "signing", run=programs, env={})
        with pytest.raises(SigningError, match="code signing"):
            signing.set_up()
        assert signing.signer() == "" and not (signing.folder / CERTIFICATE).exists()
        tmp_path = tmp_path / "next"


def test_a_step_that_fails_says_which_and_leaves_no_key_behind(tmp_path: Path) -> None:
    signing = LocalSigning(tmp_path / "signing", run=Programs(refuse="create-keychain"), env={})
    with pytest.raises(SigningError, match="could not make the keychain: create-keychain refused"):
        signing.set_up()
    assert not (signing.folder / ".key.pem").exists() and not (signing.folder / ".identity.p12").exists()


def test_a_helper_is_signed_with_this_macs_certificate_from_its_keychain_or_with_the_identity_a_person_named(
    tmp_path: Path,
) -> None:
    programs = Programs()
    signing = LocalSigning(tmp_path / "signing", run=programs, env={})
    with pytest.raises(SigningError, match="no SimMirror signing certificate"):
        signing.sign(tmp_path / "helper")
    signing.set_up()
    signing.sign(tmp_path / "helper")
    login = "/Users/a/Library/Keychains/login.keychain-db"
    lists = [call for call in programs.named("list-keychains") if "-s" in call]
    assert [call[call.index("-s") + 1 :] for call in lists] == [(login, str(signing.keychain)), (login,)], (
        "listed for the one call, then the list put back"
    )
    [signed] = programs.named(CODESIGN)
    assert signed[signed.index("--sign") + 1] == FINGERPRINT and str(signing.keychain) in signed
    assert signed[signed.index("--identifier") + 1] == "dev.simmirror.helper"
    named = LocalSigning(
        tmp_path / "other", run=(other := Programs()), env={IDENTITY_ENV: " Developer ID Application: A "}
    )
    assert named.ready() and named.signer() == "named:Developer ID Application: A"
    assert named.set_up() == "Developer ID Application: A" and not other.calls
    named.sign(tmp_path / "helper")
    [with_identity] = other.named(CODESIGN)
    assert with_identity[with_identity.index("--sign") + 1] == "Developer ID Application: A"
    assert "--keychain" not in with_identity


def test_an_unreadable_certificate_names_no_signer(tmp_path: Path) -> None:
    signing = LocalSigning(tmp_path, run=Programs(), env={})
    for text in ("no certificate here", "-----BEGIN CERTIFICATE-----\n!!\n-----END CERTIFICATE-----\n"):
        (tmp_path / CERTIFICATE).write_text(text)
        assert signing.fingerprint() is None and signing.signer() == "" and not signing.ready()


def test_programs_run_from_an_argv_with_a_timeout_and_a_missing_one_is_an_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[object] = []

    class Done:
        returncode, stdout, stderr = 0, "out\n", "err"

    def run(argv: list[str], **kwargs: object) -> Done:
        seen.append((argv, kwargs["timeout"], kwargs.get("shell", False)))
        return Done()

    monkeypatch.setattr(local_signing.subprocess, "run", run)
    assert run_program(("/usr/bin/true", "x"), 5.0) == (0, "out\nerr")
    assert seen == [(["/usr/bin/true", "x"], 5.0, False)]

    def missing(argv: list[str], **kwargs: object) -> Done:
        raise FileNotFoundError("no such program")

    monkeypatch.setattr(local_signing.subprocess, "run", missing)
    assert run_program(("/nowhere",), 5.0) == (127, "no such program")


def test_setting_up_again_after_a_lost_trust_starts_a_fresh_keychain(tmp_path: Path) -> None:
    programs = Programs()
    signing = LocalSigning(tmp_path / "signing", run=programs, env={})
    signing.set_up()
    (signing.folder / "keychain-password").unlink()
    programs.trusted = False
    assert not signing.ready()
    assert signing.set_up() == FINGERPRINT and len(programs.named("create-keychain")) == 2


def test_signing_puts_the_search_list_back_even_when_codesign_fails_and_refuses_a_list_it_cannot_read(
    tmp_path: Path,
) -> None:
    programs = Programs()
    signing = LocalSigning(tmp_path / "signing", run=programs, env={})
    signing.set_up()
    programs.refuse = CODESIGN
    with pytest.raises(SigningError, match="could not sign the helper"):
        signing.sign(tmp_path / "helper")
    assert programs.named("list-keychains")[-1][-1].endswith("login.keychain-db")
    programs.refuse, programs.no_list = "", True
    with pytest.raises(SigningError, match="could not read the keychain search list"):
        signing.sign(tmp_path / "helper")
