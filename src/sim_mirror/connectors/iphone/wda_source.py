# SPDX-License-Identifier: Apache-2.0
"""WebDriverAgent's source: the one release SimMirror builds, fetched once and checked before anything reads it.

WebDriverAgent is Appium's XCTest runner (BSD-3-Clause, `THIRD_PARTY_LICENSES.md`) that drives a real device's screen
the way Xcode's UI tests do. SimMirror builds it from source with the person's own signing team -- nothing prebuilt is
installed on a device -- and only from the release pinned here: the archive GitHub serves for that commit is refused
unless its SHA-256 is the one written below, and it is unpacked only when every entry stays inside its own folder.

`real_devices.wda.path` names a checkout of the person's own instead, used as it is.
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import tarfile
import tempfile
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

#: The Xcode project a WebDriverAgent checkout holds.
PROJECT = "WebDriverAgent.xcodeproj"
#: What a fetched release is checked against before it is read again, beside its source.
MARKER = ".sim-mirror-sha256"
#: The most a release's archive may weigh: WebDriverAgent's is about 1 MB.
ARCHIVE_MAX = 64 << 20
#: How long fetching it may take.
FETCH_TIMEOUT_S = 120.0


class WdaSourceError(Exception):
    """WebDriverAgent's source could not be had, said so a person can act on it."""


@dataclass(frozen=True)
class WdaRelease:
    """One release of WebDriverAgent: the commit it is, and the SHA-256 of the archive GitHub serves for it."""

    version: str
    commit: str
    sha256: str

    @property
    def url(self) -> str:
        return f"https://codeload.github.com/appium/WebDriverAgent/tar.gz/{self.commit}"

    @property
    def folder(self) -> str:
        """The folder the archive unpacks into."""
        return f"WebDriverAgent-{self.commit}"


#: The release SimMirror builds, measured 2026-09-29.
PINNED = WdaRelease(
    "16.12.10",
    "00c38220c3e84906c965b996ffc4c12d09fef62f",
    "27343e6064b204f7a1bff20096597d5f318564465d91e2fee66ea71bc1ddc222",
)


@dataclass(frozen=True)
class Patch:
    """A change SimMirror makes to WebDriverAgent's source: in one file, exactly one piece of text replaced."""

    path: str
    old: str
    new: str


#: WebDriverAgent binds its API where ``USE_IP`` says, but its MJPEG screen stream on every interface (FBWebServer.m,
#: 16.12.10), which would show the device's screen to anyone on its network while it runs. The stream is bound where
#: the API is -- the device's loopback -- so nothing leaves the cable.
MJPEG_ON_LOOPBACK = Patch(
    "WebDriverAgentLib/Routing/FBWebServer.m",
    "                                 initWithPort:(uint16_t)FBConfiguration.sharedInstance.mjpegServerPort];\n",
    "                                 initWithPort:(uint16_t)FBConfiguration.sharedInstance.mjpegServerPort];\n"
    "  // SimMirror: the screen stream listens only where the API does -- the device's loopback when USE_IP says so.\n"
    "  self.screenshotsBroadcaster.interface = FBConfiguration.sharedInstance.bindingIPAddress;\n",
)
PATCHES = (MJPEG_ON_LOOPBACK,)

Fetch = Callable[[str], bytes]


def download(url: str) -> bytes:
    """The body at `url`, refused past `ARCHIVE_MAX`."""
    try:
        with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT_S) as answer:
            data: bytes = answer.read(ARCHIVE_MAX + 1)
    except OSError as exc:
        raise WdaSourceError(f"WebDriverAgent could not be fetched from {url}: {exc}") from exc
    if len(data) > ARCHIVE_MAX:
        raise WdaSourceError(f"WebDriverAgent's archive at {url} is larger than {ARCHIVE_MAX >> 20} MB")
    return data


def configured(path: str) -> Path:
    """A checkout the person named, which must hold WebDriverAgent's project with SimMirror's patches made."""
    folder = Path(path).expanduser()
    if not (folder / PROJECT).is_dir():
        raise WdaSourceError(f"{folder} holds no {PROJECT} (real_devices.wda.path)")
    for patch in PATCHES:
        if patch.new not in _read(folder, patch):
            raise WdaSourceError(
                f"{folder / patch.path} does not keep the screen stream on the device's loopback: make SimMirror's "
                "change to it (docs/real-devices.md), or leave real_devices.wda.path empty to use SimMirror's copy"
            )
    return folder


def _read(folder: Path, patch: Patch) -> str:
    try:
        return (folder / patch.path).read_text(encoding="utf-8")
    except OSError:
        return ""


def patched(folder: Path, patches: tuple[Patch, ...] = PATCHES) -> None:
    """Make SimMirror's changes to an unpacked release; refuse one whose text is not exactly where it was."""
    for patch in patches:
        text = _read(folder, patch)
        if patch.new in text:
            continue
        if text.count(patch.old) != 1:
            raise WdaSourceError(f"{patch.path} is not as SimMirror knows it, so it was not changed")
        (folder / patch.path).write_text(text.replace(patch.old, patch.new), encoding="utf-8")


def stamp(release: WdaRelease, patches: tuple[Patch, ...] = PATCHES) -> str:
    """What marks an unpacked release as this archive with these changes made."""
    made = hashlib.sha256(repr(patches).encode()).hexdigest()[:12]
    return f"{release.sha256}+{made}"


def wda_source(
    root: Path, release: WdaRelease = PINNED, *, fetch: Fetch = download, patches: tuple[Patch, ...] = PATCHES
) -> Path:
    """The release's source under `root`: fetched, checked, unpacked and changed the first time, and kept."""
    folder = root / release.folder
    if (folder / PROJECT).is_dir() and _marked(folder) == stamp(release, patches):
        return folder
    data = fetch(release.url)
    digest = hashlib.sha256(data).hexdigest()
    if digest != release.sha256:
        raise WdaSourceError(
            f"WebDriverAgent {release.version}'s archive is not the one SimMirror pins: its SHA-256 is {digest}, "
            f"not {release.sha256}; nothing was unpacked"
        )
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging = Path(tempfile.mkdtemp(prefix=".wda-", dir=root))
    try:
        _unpack(data, staging, release.folder)
        staged = staging / release.folder
        if not (staged / PROJECT).is_dir():
            raise WdaSourceError(f"WebDriverAgent {release.version}'s archive holds no {PROJECT}")
        patched(staged, patches)
        (staged / MARKER).write_text(stamp(release, patches), encoding="utf-8")
        if folder.exists():
            shutil.rmtree(folder)
        os.replace(staged, folder)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return folder


def _marked(folder: Path) -> str | None:
    try:
        return (folder / MARKER).read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _unpack(data: bytes, into: Path, folder: str) -> None:
    """Unpack an archive whose every entry is a file or folder inside `folder`; refuse it whole otherwise."""
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            members = archive.getmembers()
            for member in members:
                name = PurePosixPath(member.name)
                inside = not name.is_absolute() and ".." not in name.parts and name.parts[:1] == (folder,)
                if not inside or not (member.isfile() or member.isdir()):
                    raise WdaSourceError(f"WebDriverAgent's archive holds {member.name!r}, which is not unpacked")
            # Written by hand rather than extracted: only folders and plain files, with modes of SimMirror's own.
            for member in members:
                target = into.joinpath(*PurePosixPath(member.name).parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True, mode=0o755)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                source = archive.extractfile(member)
                assert source is not None  # a plain file always has contents
                target.write_bytes(source.read())
                # Only whether it runs is kept of its mode: its build scripts do.
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise WdaSourceError(f"WebDriverAgent's archive could not be unpacked: {exc}") from exc
