# SPDX-License-Identifier: Apache-2.0
"""The app in front of a simulator, read for the view hierarchy it shares.

Several apps on a simulator may have listed themselves, and only the one in front has anything on screen to say. All
of them are asked at once -- those that said they were in front first, at most `wire.ASKED_MAX` -- and the first to
answer with a hierarchy is read: an app that is not in front answers at once that it is not, so waiting is only ever
for the app that is (`ask_apps`).

Between snapshots `AppReader` remembers which listings to pass over: one whose app has gone or no longer knows its
secret, until the listing is written again, and one whose app did not answer in time -- suspended in the background, or
paused in a debugger -- for `wire.SUSPENDED_S`, so a slow app does not slow every snapshot. `probe` asks without
remembering, for the doctor and the command line.

Sharing is optional, so no app sharing a hierarchy is nothing to say. A snapshot only hears of an app that is in front
and could not be read, of a hierarchy cut short, or of an app speaking a newer protocol.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from sim_mirror.connectors.app import wire
from sim_mirror.connectors.app.client import fetch_hierarchy
from sim_mirror.connectors.app.discovery import AppListing, find_listings
from sim_mirror.connectors.app.document import AppDocument, SharedApp, document_from_app
from sim_mirror.connectors.app.errors import AppInactive, AppRefused, AppSdkError, AppTimedOut, AppUnreachable
from sim_mirror.host_copy import HostCopy
from sim_mirror.perception.model import ScreenTree
from sim_mirror.perception.readers import tree_from_document

Fetch = Callable[..., Awaitable[dict[str, Any]]]
#: Refusals that mean a listing is out of date -- its app no longer knows that secret, or that route.
STALE_STATUSES = frozenset({401, 404, 405})


class Find(Protocol):
    def __call__(self, data_dir: Path, udid: str) -> list[AppListing]: ...


@dataclass(frozen=True)
class Asked:
    """What asking a simulator's apps found: the app that shared its hierarchy, if any, and why each other did not."""

    listing: AppListing | None
    document: AppDocument | None
    failures: tuple[tuple[AppListing, AppSdkError], ...] = ()


def _order(listings: Sequence[AppListing]) -> list[AppListing]:
    """Those that said they were in front first, then the most recently written."""
    return sorted(listings, key=lambda listing: (not listing.active, -listing.modified_ns))[: wire.ASKED_MAX]


async def ask_apps(listings: Sequence[AppListing], *, max_nodes: int, timeout_s: float, fetch: Fetch) -> Asked:
    """Ask every app at once; the first to answer with a hierarchy is the one read, and the others are let go."""
    ordered = _order(listings)

    async def ask(listing: AppListing) -> AppDocument:
        raw = await fetch(listing, max_nodes=max_nodes, timeout_s=timeout_s)
        return document_from_app(raw, bundle_id=listing.bundle_id, pid=listing.pid, max_nodes=max_nodes)

    tasks = {asyncio.ensure_future(ask(listing)): listing for listing in ordered}
    pending = set(tasks)
    failures: list[tuple[AppListing, AppSdkError]] = []
    try:
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in sorted(done, key=lambda each: ordered.index(tasks[each])):
                error = task.exception()
                if error is None:
                    return Asked(tasks[task], task.result(), tuple(failures))
                if not isinstance(error, AppSdkError):
                    raise error
                failures.append((tasks[task], error))
    finally:
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    return Asked(None, None, tuple(failures))


@dataclass(frozen=True)
class AppProbe:
    """What a simulator's apps say, asked once: every listing found, and what asking them found."""

    listings: tuple[AppListing, ...]
    asked: Asked
    took_s: float


async def probe(
    udid: str,
    data_dir: Path,
    *,
    max_nodes: int,
    timeout_s: float,
    fetch: Fetch = fetch_hierarchy,
    find: Find = find_listings,
    clock: Callable[[], float] = time.monotonic,
) -> AppProbe:
    """Find a simulator's apps and ask them, remembering nothing: for the doctor and the command line."""
    began = clock()
    listings = await asyncio.to_thread(find, data_dir, udid)
    asked = await ask_apps(listings, max_nodes=max_nodes, timeout_s=timeout_s, fetch=fetch)
    return AppProbe(tuple(listings), asked, clock() - began)


@dataclass
class AppMemory:
    """What one simulator's snapshots remember of its apps between them."""

    #: Listings whose app has gone or no longer knows their secret, by when each was written.
    passed: dict[Path, int] = field(default_factory=dict)
    #: Listings whose app did not answer in time: when each was written, and until when it is left alone.
    resting: dict[Path, tuple[int, float]] = field(default_factory=dict)
    #: The app that shared its hierarchy at the last snapshot.
    shared: SharedApp | None = None


def _refused_stale(error: AppSdkError) -> bool:
    """A refusal that means the listing is out of date."""
    return isinstance(error, AppRefused) and error.status in STALE_STATUSES


def _stale(error: AppSdkError) -> bool:
    """Whether a listing is not worth asking again until its app writes it anew."""
    if isinstance(error, AppRefused):
        return _refused_stale(error)
    return not isinstance(error, AppTimedOut | AppInactive)


class AppReader:
    """The hierarchy the app in front of a simulator shares, read for one snapshot and remembered for the next."""

    def __init__(
        self,
        udid: str,
        *,
        data_dir: Path,
        memory: AppMemory,
        max_nodes: int,
        timeout_s: float,
        copy: HostCopy,
        on_share: Callable[[SharedApp | None], None],
        fetch: Fetch = fetch_hierarchy,
        find: Find = find_listings,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._udid = udid
        self._data_dir = data_dir
        self._memory = memory
        self._max_nodes = max_nodes
        self._timeout_s = timeout_s
        self._copy = copy
        self._on_share = on_share
        self._fetch = fetch
        self._find = find
        self._clock = clock

    def _worth_asking(self, listing: AppListing) -> bool:
        memory, now = self._memory, self._clock()
        if memory.passed.get(listing.path) == listing.modified_ns:
            return False
        resting = memory.resting.get(listing.path)
        return resting is None or resting[0] != listing.modified_ns or now >= resting[1]

    def _remember(self, listings: Sequence[AppListing], asked: Asked) -> None:
        memory, present = self._memory, {listing.path for listing in listings}
        memory.passed = {path: written for path, written in memory.passed.items() if path in present}
        memory.resting = {path: rest for path, rest in memory.resting.items() if path in present}
        if asked.listing is not None:
            memory.resting.pop(asked.listing.path, None)
        for listing, error in asked.failures:
            if isinstance(error, AppTimedOut):
                memory.resting[listing.path] = (listing.modified_ns, self._clock() + wire.SUSPENDED_S)
            elif _stale(error):
                memory.passed[listing.path] = listing.modified_ns

    def _share(self, app: SharedApp | None) -> None:
        if self._memory.shared != app:
            self._memory.shared = app
            self._on_share(app)

    def _notes(self, asked: Asked) -> tuple[str, ...]:
        if asked.listing is None or asked.document is None:
            return tuple(
                self._copy.app_hierarchy_unread(listing.name, str(error))
                for listing, error in asked.failures
                if listing.active and not isinstance(error, AppInactive | AppUnreachable) and not _refused_stale(error)
            )
        document, notes = asked.document, []
        if document.truncated:
            notes.append(self._copy.app_hierarchy_cut(document.app.name, self._max_nodes))
        if document.protocol > wire.PROTOCOL_VERSION:
            notes.append(self._copy.app_sdk_newer(document.app.name, document.protocol))
        return tuple(notes)

    async def read(self) -> ScreenTree:
        listings = await asyncio.to_thread(self._find, self._data_dir, self._udid)
        wanted = [listing for listing in listings if self._worth_asking(listing)]
        asked = await ask_apps(wanted, max_nodes=self._max_nodes, timeout_s=self._timeout_s, fetch=self._fetch)
        self._remember(listings, asked)
        self._share(asked.document.app if asked.document is not None else None)
        notes = self._notes(asked)
        if asked.document is None:
            return ScreenTree(notes=notes)
        tree = tree_from_document(asked.document.document, source=wire.SOURCE)
        return ScreenTree(roots=tree.roots, modal=tree.modal, notes=notes)
