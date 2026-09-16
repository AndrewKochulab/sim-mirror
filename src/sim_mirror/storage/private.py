# SPDX-License-Identifier: Apache-2.0
"""Folders and files only their owner can read, files written whole or not at all, and secrets kept in them.

A secret -- the daemon's admin token -- has to outlive the process that made it, so it is kept on disk: random bytes
as hex, mode 0600, in a 0700 folder. One that cannot be read, is the wrong length, or was opened up to others is
replaced rather than trusted (anything made with it stops verifying, which is the point) -- except on a mount that
cannot keep any file private, such as SMB or exFAT, where a new one would look just as open and be replaced on every
read.
"""

from __future__ import annotations

import contextlib
import fcntl
import logging
import os
import secrets
import stat
import tempfile
from collections.abc import Iterator
from pathlib import Path

logger = logging.getLogger(__name__)

SECRET_BYTES = 32


def ensure_private_dir(folder: Path) -> Path:
    """Make a folder only its owner can enter, and answer it."""
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    with contextlib.suppress(OSError):
        folder.chmod(0o700)
    return folder


def write_atomic(path: Path, data: bytes, *, mode: int | None = None) -> None:
    """Write a file whole or -- when writing fails -- not at all, and have it survive a crash straight after.

    The data goes to a temporary file of a name no other write shares, is flushed to disk, and replaces the file in one
    step; the folder is flushed too, so the replacement itself is kept. The file keeps the mode it had -- a person's
    file stays theirs -- unless `mode` is given, and a new one is made for its owner only.
    """
    try:
        kept = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        kept = 0o600
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), kept if mode is None else mode)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise
    folder = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(folder)
    finally:
        os.close(folder)


def write_private(path: Path, data: bytes) -> None:
    """Write a file only its owner can read, in a folder only its owner can enter: all of it, or none of it."""
    ensure_private_dir(path.parent)
    write_atomic(path, data, mode=0o600)


@contextlib.contextmanager
def file_lock(path: Path, *, private_folder: bool = True) -> Iterator[None]:
    """Hold an exclusive lock on `path` -- made if missing -- for the block, against every process on the Mac.

    Its folder is made private, unless `private_folder` is false: a lock beside a person's own file, in a folder of
    theirs, leaves that folder as it was. It blocks the thread while another process holds it: never await inside the
    block.
    """
    if private_folder:
        ensure_private_dir(path.parent)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def read_or_create_secret(path: Path, *, size: int = SECRET_BYTES) -> bytes:
    """The secret at `path`, made first when there is none or what is there cannot be trusted."""
    with file_lock(path.with_name(f"{path.name}.lock")):
        try:
            raw = bytes.fromhex(path.read_text(encoding="ascii").strip())
            if len(raw) == size and _still_private(path):
                return raw
        except (OSError, ValueError):
            pass
        return _write_secret(path, secrets.token_bytes(size))


def replace_secret(path: Path, *, size: int = SECRET_BYTES) -> bytes:
    """A new secret at `path`: everything made with the old one stops verifying."""
    with file_lock(path.with_name(f"{path.name}.lock")):
        return _write_secret(path, secrets.token_bytes(size))


def _write_secret(path: Path, raw: bytes) -> bytes:
    write_private(path, (raw.hex() + "\n").encode("ascii"))
    return raw


def _still_private(path: Path) -> bool:
    if not stat.S_IMODE(path.stat().st_mode) & 0o077:
        return True
    if not holds_private_files(path.parent):
        logger.warning("%s looks readable by others, but its folder cannot keep a file private; it is kept", path)
        return True
    logger.warning("%s was readable by others, so it is replaced", path)
    return False


def holds_private_files(folder: Path) -> bool:
    """Whether a file made owner-only in `folder` stays owner-only."""
    probe = folder / f".probe.{os.getpid()}.tmp"
    descriptor = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        return not stat.S_IMODE(os.fstat(descriptor).st_mode) & 0o077
    finally:
        os.close(descriptor)
        probe.unlink()
