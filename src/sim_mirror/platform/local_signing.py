# SPDX-License-Identifier: Apache-2.0
"""A signing identity of this Mac's own, so a permission granted to the installed helper survives an upgrade.

macOS keeps a Screen Recording or Accessibility grant for a program as its path and its *designated requirement*. A
helper signed ad hoc has its hash as that requirement, so every new version is a stranger. A helper signed with a
certificate has the certificate in it instead: every version signed with the same certificate is the same program.

Without a paid developer identity there is no such certificate, so SimMirror makes one for this Mac, once
(`LocalSigning.set_up`): a key and a self-signed code-signing certificate kept in a keychain of SimMirror's own, in
SimMirror's state folder, whose password is a random file only its owner can read -- the login keychain is never
touched, and SimMirror's is on the keychain search list only while codesign signs with it. macOS uses a certificate to
sign code only once it is trusted for that, so setting up asks the person once, with macOS's own password dialog, to
trust it for code signing. Each helper installed after that
is signed with it (`LocalSigning.sign`).

A person or a build that has a real identity names it instead (``SIM_MIRROR_HELPER_SIGN_IDENTITY``), and the helper is
signed with that from their own keychains -- a developer ID later needs nothing else.

Every program here runs from an argv, never a shell, with a timeout.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import secrets
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from sim_mirror.storage.private import ensure_private_dir, write_atomic

#: The identifier every SimMirror helper is signed with.
HELPER_IDENTIFIER = "dev.simmirror.helper"
#: A signing identity a person or a build names, from their own keychains, instead of this Mac's own.
IDENTITY_ENV = "SIM_MIRROR_HELPER_SIGN_IDENTITY"
KEYCHAIN = "signing.keychain-db"
PASSWORD = "keychain-password"
CERTIFICATE = "certificate.pem"
COMMON_NAME = "SimMirror helper signing (this Mac)"
#: Signing hashes a few megabytes; trusting waits for a person at macOS's password dialog.
SIGN_TIMEOUT_S = 60.0
TRUST_TIMEOUT_S = 300.0
QUERY_TIMEOUT_S = 20.0
OPENSSL = "/usr/bin/openssl"
SECURITY = "/usr/bin/security"
CODESIGN = "/usr/bin/codesign"

#: Runs an argv; answers its exit status and what it printed, both streams together. Injectable for tests.
Run = Callable[[Sequence[str], float], tuple[int, str]]


def run_program(argv: Sequence[str], timeout: float) -> tuple[int, str]:
    try:
        done = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)
    return done.returncode, (done.stdout + done.stderr).strip()


class SigningError(Exception):
    """The helper could not be signed, or signing could not be set up -- said so a person can act on it."""


@dataclass(frozen=True)
class LocalSigning:
    """This Mac's own signing identity, in `folder` (``<state>/signing``)."""

    folder: Path
    run: Run = run_program
    env: Mapping[str, str] = field(default_factory=lambda: os.environ)

    @property
    def keychain(self) -> Path:
        return self.folder / KEYCHAIN

    @property
    def configured(self) -> str:
        """The identity a person or a build named, else empty."""
        return self.env.get(IDENTITY_ENV, "").strip()

    def fingerprint(self) -> str | None:
        """This Mac's certificate as its SHA-1 -- how codesign names an identity -- or None when there is none. Read
        without running anything, so asking is cheap."""
        try:
            pem = (self.folder / CERTIFICATE).read_text()
            body = pem.split("-----BEGIN CERTIFICATE-----", 1)[1].split("-----END CERTIFICATE-----", 1)[0]
            der = base64.b64decode("".join(body.split()), validate=True)
        except (OSError, IndexError, ValueError, binascii.Error):
            return None
        return hashlib.sha1(der).hexdigest().upper() if der else None

    def signer(self) -> str:
        """Who signs an installed helper now -- the named identity, this Mac's certificate, or nobody (as shipped) --
        so a copy signed otherwise is signed again."""
        if self.configured:
            return f"named:{self.configured}"
        found = self.fingerprint()
        return f"local:{found}" if found else ""

    def ready(self) -> bool:
        """Whether a helper would be signed by a certificate: a named identity, or this Mac's, trusted for code."""
        if self.configured:
            return True
        found = self.fingerprint()
        if found is None or not self.keychain.is_file():
            return False
        self._unlock()
        code, out = self.run(
            (SECURITY, "find-identity", "-v", "-p", "codesigning", str(self.keychain)), QUERY_TIMEOUT_S
        )
        return code == 0 and found in out.upper()

    def set_up(self) -> str:
        """Make this Mac's identity, once, and have the person trust it for code signing. Answers its fingerprint."""
        if self.ready():
            return self.configured or str(self.fingerprint())
        ensure_private_dir(self.folder)
        password = secrets.token_hex(32)
        key, bundle = self.folder / ".key.pem", self.folder / ".identity.p12"
        certificate = self.folder / CERTIFICATE
        transfer = secrets.token_hex(16)
        try:
            self._must(
                (OPENSSL, "req", "-x509", "-newkey", "rsa:3072", "-nodes", "-days", "3650",
                 "-subj", f"/CN={COMMON_NAME}", "-addext", "keyUsage=critical,digitalSignature",
                 "-addext", "extendedKeyUsage=critical,codeSigning",
                 "-addext", "basicConstraints=critical,CA:false", "-keyout", str(key), "-out", str(certificate)),
                "make the certificate",
            )  # fmt: skip
            key.chmod(0o600)
            self._must(
                (OPENSSL, "pkcs12", "-export", "-inkey", str(key), "-in", str(certificate), "-out", str(bundle),
                 "-passout", f"pass:{transfer}"),
                "bundle the key",
            )  # fmt: skip
            if self.keychain.exists():
                self.keychain.unlink()
            write_atomic(self.folder / PASSWORD, password.encode(), mode=0o600)
            self._must((SECURITY, "create-keychain", "-p", password, str(self.keychain)), "make the keychain")
            self._must((SECURITY, "set-keychain-settings", str(self.keychain)), "keep the keychain unlocked")
            self._unlock()
            self._must(
                (SECURITY, "import", str(bundle), "-k", str(self.keychain), "-P", transfer, "-T", CODESIGN),
                "keep the key",
            )  # fmt: skip
            self._must(
                (SECURITY, "set-key-partition-list", "-S", "apple-tool:,apple:,codesign:", "-s", "-k", password,
                 str(self.keychain)),
                "let codesign use the key",
            )  # fmt: skip
        finally:
            key.unlink(missing_ok=True)
            bundle.unlink(missing_ok=True)
        try:
            # macOS asks the person here, once, with its own password dialog.
            self._must(
                (SECURITY, "add-trusted-cert", "-r", "trustRoot", "-p", "codeSign", "-k", str(self.keychain),
                 str(certificate)),
                "trust the certificate for code signing (macOS asked for your password)",
                timeout=TRUST_TIMEOUT_S,
            )  # fmt: skip
            found = self.fingerprint()
            if found is None or not self.ready():
                raise SigningError("the certificate was made, but macOS does not use it for code signing")
        except SigningError:
            # A certificate nobody trusts is no signer: helpers stay as shipped rather than failing to sign each time.
            certificate.unlink(missing_ok=True)
            raise
        return found

    def sign(self, binary: Path) -> None:
        """Sign `binary` with the named identity, else this Mac's; `SigningError` when neither can."""
        argv: tuple[str, ...]
        if self.configured:
            argv = (CODESIGN, "--force", "--sign", self.configured, "--identifier", HELPER_IDENTIFIER,
                    "--timestamp=none", str(binary))  # fmt: skip
        else:
            found = self.fingerprint()
            if found is None:
                raise SigningError("this Mac has no SimMirror signing certificate")
            self._unlock()
            argv = (CODESIGN, "--force", "--sign", found, "--keychain", str(self.keychain), "--identifier",
                    HELPER_IDENTIFIER, "--timestamp=none", str(binary))  # fmt: skip
            # codesign finds an identity only in a keychain on the user's search list, --keychain or not: SimMirror's
            # is put on it for this one call and the list put back as it was, whatever happens.
            listed = self._search_list()
            self._must(
                (SECURITY, "list-keychains", "-d", "user", "-s", *listed, str(self.keychain)),
                "list SimMirror's keychain for signing",
            )
            try:
                self._must(argv, "sign the helper", timeout=SIGN_TIMEOUT_S)
            finally:
                self.run((SECURITY, "list-keychains", "-d", "user", "-s", *listed), QUERY_TIMEOUT_S)
            return
        self._must(argv, "sign the helper", timeout=SIGN_TIMEOUT_S)

    def _search_list(self) -> list[str]:
        """The user's keychain search list as it is, without SimMirror's own."""
        code, out = self.run((SECURITY, "list-keychains", "-d", "user"), QUERY_TIMEOUT_S)
        if code != 0:
            raise SigningError(f"could not read the keychain search list: {out or f'exit {code}'}")
        listed = [line.strip().strip('"') for line in out.splitlines() if line.strip()]
        return [path for path in listed if path != str(self.keychain)]

    def _unlock(self) -> None:
        password = self.folder / PASSWORD
        if password.is_file() and self.keychain.is_file():
            self.run(
                (SECURITY, "unlock-keychain", "-p", password.read_text().strip(), str(self.keychain)), QUERY_TIMEOUT_S
            )

    def _must(self, argv: Sequence[str], doing: str, *, timeout: float = QUERY_TIMEOUT_S) -> None:
        code, out = self.run(argv, timeout)
        if code != 0:
            raise SigningError(f"could not {doing}: {out or f'exit {code}'}")
