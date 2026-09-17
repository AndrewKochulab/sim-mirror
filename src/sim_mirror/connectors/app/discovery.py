# SPDX-License-Identifier: Apache-2.0
"""The apps on a simulator that say they share their view hierarchy.

A running app built with the debug SDK writes a listing -- its port, its process, a secret new each launch -- under
the simulator's data folder, or under its own container when that cannot be written (``protocol/app-sdk/v1``). A
listing is only believed as far as the Mac can vouch for it: a regular file, not a link, owned by the user SimMirror
runs as and readable by nobody else, small, for this simulator, named for the app it describes, and written by a
process that is still there. Anything else is passed over, and never deleted: the file is the app's.
"""

from __future__ import annotations

import json
import logging
import os
import re
import stat
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sim_mirror.connectors.app import wire
from sim_mirror.platform import process

logger = logging.getLogger(__name__)

_SDK_VERSION = re.compile(r"\A[0-9A-Za-z.+-]{1,32}\Z")
_BUNDLE_ID = re.compile(r"\A[A-Za-z0-9.-]{1,155}\Z")
_SECRET = re.compile(r"\A[A-Za-z0-9_-]{32,128}\Z")


@dataclass(frozen=True)
class AppListing:
    """An app's word that it listens on 127.0.0.1, and what it asks a request to carry."""

    path: Path
    #: When the listing was last written; an app writes it again whenever it comes to the front or leaves it.
    modified_ns: int
    protocol: int
    sdk_version: str
    bundle_id: str
    name: str
    pid: int
    port: int
    active: bool
    secret: str = field(repr=False)


def _whole(value: Any, low: int, high: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        return None
    return value


def _trusted(info: os.stat_result, uid: int) -> bool:
    """A regular file of this user's, readable by nobody else, small enough to be a listing."""
    return (
        stat.S_ISREG(info.st_mode)
        and info.st_uid == uid
        and not info.st_mode & 0o077
        and info.st_size <= wire.LISTING_MAX_BYTES
    )


def _load(path: Path, uid: int) -> tuple[dict[str, Any], int] | None:
    """The listing file's content and when it was written, when the file can be trusted."""
    try:
        if not _trusted(os.lstat(path), uid):
            return None
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as file:
            info = os.fstat(file.fileno())
            if not _trusted(info, uid):
                return None
            raw = json.loads(file.read(wire.LISTING_MAX_BYTES + 1))
    except (OSError, ValueError, RecursionError):
        return None
    return (raw, info.st_mtime_ns) if isinstance(raw, dict) else None


def read_listing(path: Path, udid: str, *, uid: int) -> AppListing | None:
    """The listing at `path`, when it can be trusted and says what a listing for this simulator says; None otherwise."""
    loaded = _load(path, uid)
    if loaded is None:
        return None
    raw, modified_ns = loaded
    bundle_id, sdk_version, secret = raw.get("bundle_id"), raw.get("sdk_version"), raw.get("secret")
    protocol = _whole(raw.get("protocol"), 1, 2**31)
    pid = _whole(raw.get("pid"), 1, 2**31)
    port = _whole(raw.get("port"), 1024, 65535)
    if (
        not isinstance(bundle_id, str)
        or not _BUNDLE_ID.match(bundle_id)
        or path.name != f"{bundle_id}.json"
        or raw.get("device_udid") != udid
        or not isinstance(sdk_version, str)
        or not _SDK_VERSION.match(sdk_version)
        or not isinstance(secret, str)
        or not _SECRET.match(secret)
        or protocol is None
        or pid is None
        or port is None
    ):
        logger.debug("passing over an app listing that does not say what one says: %s", path)
        return None
    name = raw.get("name")
    return AppListing(
        path=path,
        modified_ns=modified_ns,
        protocol=protocol,
        sdk_version=sdk_version,
        bundle_id=bundle_id,
        name=name[: wire.NAME_MAX] if isinstance(name, str) and name else bundle_id,
        pid=pid,
        port=port,
        active=raw.get("active") is True,
        secret=secret,
    )


def _listing_files(data_dir: Path) -> Iterator[Path]:
    folders = [data_dir.joinpath(*wire.LISTINGS)]
    folders.extend(sorted(data_dir.joinpath(*wire.CONTAINERS).glob(str(Path("*", *wire.LISTINGS)))))
    for folder in folders:
        try:
            yield from (path for path in folder.iterdir() if path.suffix == ".json" and not path.name.startswith("."))
        except OSError:
            continue


def _modified(path: Path) -> int:
    try:
        return os.lstat(path).st_mtime_ns
    except OSError:
        return 0


def find_listings(
    data_dir: Path,
    udid: str,
    *,
    pid_alive: Callable[[int], bool] = process.pid_alive,
    uid: int | None = None,
) -> list[AppListing]:
    """The listings of apps still running on a simulator, newest first, one per app, at most `wire.LISTINGS_MAX`."""
    files = sorted(_listing_files(data_dir), key=_modified, reverse=True)[: wire.LISTINGS_MAX]
    owner = os.getuid() if uid is None else uid
    found: dict[str, AppListing] = {}
    for path in files:
        listing = read_listing(path, udid, uid=owner)
        if listing is None or listing.bundle_id in found or not pid_alive(listing.pid):
            continue
        found[listing.bundle_id] = listing
    return list(found.values())
