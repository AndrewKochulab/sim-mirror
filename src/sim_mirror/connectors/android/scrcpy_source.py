# SPDX-License-Identifier: Apache-2.0
"""scrcpy's server: the one release SimMirror speaks to, fetched once when a person sets it up, and checked.

scrcpy (Genymobile, Apache-2.0, `THIRD_PARTY_LICENSES.md`) streams an Android device's screen as H.264 and takes its
touches, keys and text, from a small server it runs on the device (``app_process``). Its client and server must be the
same release -- the server refuses any other -- so SimMirror speaks one, pinned below by version, size and SHA-256 as
GitHub publishes them.

Nothing is fetched until a person asks: Set up touch in the viewer, or ``sim-mirror android setup``. The release is
written to SimMirror's state folder only once its hash is the pinned one; a download that is not is thrown away.
``android.scrcpy.path`` names a copy of the person's own instead -- Homebrew's, say -- used only when its hash is a
release SimMirror knows.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sim_mirror.storage.app_support import state_dir
from sim_mirror.storage.private import ensure_private_dir

#: The most a server may weigh: 5.0.1's is 734 KB.
JAR_MAX = 8 << 20
#: How long fetching it may take.
FETCH_TIMEOUT_S = 120.0


class ScrcpySourceError(Exception):
    """scrcpy's server could not be had, said so a person can act on it."""


@dataclass(frozen=True)
class ScrcpyRelease:
    """One release of scrcpy's server, as GitHub publishes it."""

    version: str
    size: int
    sha256: str

    @property
    def url(self) -> str:
        return f"https://github.com/Genymobile/scrcpy/releases/download/v{self.version}/scrcpy-server-v{self.version}"

    @property
    def file_name(self) -> str:
        return f"scrcpy-server-v{self.version}"


#: The release SimMirror speaks to, measured 2026-10-25: GitHub's own digest of the asset.
PINNED = ScrcpyRelease("5.0.1", 733930, "764eb6f79811d5211fe9df341120882ba9994c7a61b897d7bf3fb662e53bc536")
#: Every release SimMirror speaks to, by its hash.
KNOWN = {PINNED.sha256: PINNED}


@dataclass(frozen=True)
class Jar:
    """A server on this Mac, and the release it is."""

    path: Path
    release: ScrcpyRelease


def scrcpy_root() -> Path:
    """Where SimMirror keeps the server it fetched."""
    return state_dir(os.environ) / "scrcpy"


def download(url: str) -> bytes:
    """The body at `url`, refused past `JAR_MAX`."""
    try:
        with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT_S) as answer:
            data: bytes = answer.read(JAR_MAX + 1)
    except OSError as exc:
        raise ScrcpySourceError(f"scrcpy's server could not be fetched from {url}: {exc}") from exc
    if len(data) > JAR_MAX:
        raise ScrcpySourceError(f"scrcpy's server at {url} is larger than {JAR_MAX >> 20} MB")
    return data


class Hashes:
    """Files' SHA-256, read again only when a file's size or modification time changes: a server is asked about on
    every probe."""

    def __init__(self) -> None:
        self._known: dict[Path, tuple[int, int, str]] = {}

    def of(self, path: Path) -> str | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        known = self._known.get(path)
        if known is not None and known[:2] == (stat.st_size, stat.st_mtime_ns):
            return known[2]
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return None
        self._known[path] = (stat.st_size, stat.st_mtime_ns, digest)
        return digest


Fetch = Callable[[str], bytes]


@dataclass
class Fetching:
    """A person's setup of scrcpy's server: under way, done or failed, and what it said last."""

    state: str = "fetching"
    said: str = ""
    task: asyncio.Task[None] | None = None


class ScrcpyJars:
    """scrcpy's server on this Mac: the one named, else the one fetched -- and fetching it, once a person asks."""

    def __init__(
        self,
        *,
        root: Callable[[], Path] = scrcpy_root,
        fetch: Fetch = download,
        release: ScrcpyRelease = PINNED,
    ) -> None:
        self._root = root
        self._fetch = fetch
        self.release = release
        #: The releases spoken to: every one SimMirror knows, and the one it fetches.
        self._known = {**KNOWN, release.sha256: release}
        self._hashes = Hashes()
        #: The setup under way or last done, if any.
        self.fetching: Fetching | None = None
        #: Told once a setup has the server, so sessions waiting for it can start it.
        self.on_ready: Callable[[], object] | None = None

    def _verified(self, path: Path) -> Jar | None:
        release = self._known.get(self._hashes.of(path) or "")
        return None if release is None else Jar(path, release)

    def find(self, configured: str = "") -> Jar | None:
        """The server to use: the one ``android.scrcpy.path`` names, if a known release; else the one fetched."""
        if configured:
            return self._verified(Path(configured).expanduser())
        return self._verified(self._root() / self.release.file_name)

    def why_not(self, configured: str = "") -> str | None:
        """Why there is no server to use; None when there is."""
        if self.find(configured) is not None:
            return None
        if configured:
            return (
                f"{configured} (android.scrcpy.path) is not a scrcpy server SimMirror knows: SimMirror speaks to "
                f"release {self.release.version}"
            )
        return "scrcpy's server is not set up: set it up once (Set up touch, or sim-mirror android setup)"

    def start(self) -> Fetching:
        """Fetch the pinned server -- or answer the fetch under way."""
        if self.fetching is not None and self.fetching.state == "fetching":
            return self.fetching
        fetching = Fetching(said=f"Fetching scrcpy's server, release {self.release.version}.")
        fetching.task = asyncio.get_running_loop().create_task(self._run(fetching))
        self.fetching = fetching
        return fetching

    async def _run(self, fetching: Fetching) -> None:
        try:
            await asyncio.to_thread(self.fetch_now)
        except ScrcpySourceError as exc:
            fetching.state, fetching.said = "failed", str(exc)
            return
        fetching.state, fetching.said = "ready", f"scrcpy's server {self.release.version} is set up."
        if self.on_ready is not None:
            self.on_ready()

    def fetch_now(self) -> Jar:
        """Fetch the pinned server, check it, and keep it: blocks."""
        data = self._fetch(self.release.url)
        digest = hashlib.sha256(data).hexdigest()
        if digest != self.release.sha256 or len(data) != self.release.size:
            raise ScrcpySourceError(
                f"the scrcpy server fetched from {self.release.url} is not release {self.release.version} "
                f"(SHA-256 {digest[:16]}…, {len(data)} bytes), so it was thrown away"
            )
        folder = ensure_private_dir(self._root())
        target = folder / self.release.file_name
        partial = target.with_suffix(".partial")
        partial.write_bytes(data)
        os.replace(partial, target)
        return Jar(target, self.release)

    async def shutdown(self) -> None:
        """End a fetch under way."""
        if self.fetching is not None and self.fetching.task is not None:
            self.fetching.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.fetching.task
