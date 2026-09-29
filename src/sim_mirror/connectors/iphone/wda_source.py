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
    #: Whether a checkout of the person's own must carry it too: one that keeps the device safe must, one that only
    #: makes WebDriverAgent quicker need not.
    required: bool = True


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
#: WebDriverAgent looks every point of a touch up against the app on screen (FBW3CActionsSynthesizer.m, 16.12.10) --
#: an accessibility round trip of about 240 ms a point. On an iPhone an app fills the screen, so a point given in the
#: viewport is where it is on the screen, and is used as given. Measured on an iPhone 14 Pro: a tap took 0.68 s, and
#: 0.34 s after; a 0.5 s drag of four points 1.7 to 2.3 s, and 0.85 s. It is only quicker, never safer, so a checkout of
#: the person's own need not carry it.
SYNTHESIZER = "WebDriverAgentLib/Utilities/FBW3CActionsSynthesizer.m"
_MOVE_ITEM = "@interface FBPointerMoveItem : FBW3CGestureItem\n\n@end\n"
_SCREEN_POINT = """
// SimMirror: a point given in the viewport is where it is on the screen -- an iPhone's app fills the screen -- so it is
// used as given, not looked up against the app, an accessibility round trip of about 240 ms a point. A point given
// any other way is looked up as before.
static CGPoint FBSimMirrorScreenPoint(FBBaseGestureItem *item)
{
  NSDictionary<NSString *, id> *action = item.actionItem;
  if (![[action objectForKey:FB_ACTION_ITEM_KEY_TYPE] isEqualToString:FB_ACTION_ITEM_TYPE_POINTER_MOVE]) {
    FBBaseGestureItem *previous =
      [item isKindOfClass:FBW3CGestureItem.class] ? ((FBW3CGestureItem *)item).previousItem : nil;
    return nil == previous ? item.atPosition.screenPoint : FBSimMirrorScreenPoint(previous);
  }
  id origin = [action objectForKey:FB_ACTION_ITEM_KEY_ORIGIN] ?: FB_ORIGIN_TYPE_VIEWPORT;
  id x = [action objectForKey:FB_ACTION_ITEM_KEY_X];
  id y = [action objectForKey:FB_ACTION_ITEM_KEY_Y];
  if ([origin isKindOfClass:NSString.class] && [origin isEqualToString:FB_ORIGIN_TYPE_VIEWPORT]
      && [x isKindOfClass:NSNumber.class] && [y isKindOfClass:NSNumber.class]) {
    return CGPointMake([x doubleValue], [y doubleValue]);
  }
  return item.atPosition.screenPoint;
}
"""
_DOWN_AT = (
    "[[XCPointerEventPath alloc] initForTouchAtPoint:{}\n"
    "                                                                          offset:FBMillisToSeconds(self.offset)];"
)
_OPENED_AT = "    return @[[[XCPointerEventPath alloc] initForTouchAtPoint:{}\n"
_MOVED_TO = "  [eventPath moveToPoint:{}\n"
_LOOKED_UP, _AS_GIVEN = "self.atPosition.screenPoint", "FBSimMirrorScreenPoint(self)"
POINTS_AS_GIVEN = (
    Patch(SYNTHESIZER, _MOVE_ITEM, _MOVE_ITEM + _SCREEN_POINT, required=False),
    *(
        Patch(SYNTHESIZER, place.format(_LOOKED_UP), place.format(_AS_GIVEN), required=False)
        for place in (_DOWN_AT, _OPENED_AT, _MOVED_TO)
    ),
)
PATCHES = (MJPEG_ON_LOOPBACK, *POINTS_AS_GIVEN)

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
        if patch.required and patch.new not in _read(folder, patch):
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
