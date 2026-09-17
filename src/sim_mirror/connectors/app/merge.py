# SPDX-License-Identifier: Apache-2.0
"""An app's shared view hierarchy merged into the snapshots of the device it runs on.

With ``connectors.app.merge`` on -- the default, which costs nothing while no app shares a hierarchy -- a snapshot reads
the connector's accessibility tree whole, and from the hierarchy of the app in front adds what accessibility left out
(`perception.readers.MergedReader`). With ``connectors.app.name_unlabeled`` on too, the app's labels also name what
accessibility found without one (`perception.readers.NamingReader`). Both are read on every snapshot, like every
setting. Each device's memory of its apps is kept until the device ends, and every screen watching it is told which
app shares its hierarchy.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.app.client import fetch_hierarchy
from sim_mirror.connectors.app.discovery import find_listings
from sim_mirror.connectors.app.document import SharedApp
from sim_mirror.connectors.app.reader import AppMemory, AppReader, Fetch, Find
from sim_mirror.host_copy import HostCopy
from sim_mirror.perception.readers import NamingReader, TreeReader
from sim_mirror.platform.device_data import device_data_dir
from sim_mirror.protocol import AppHierarchy

OnShare = Callable[[str, AppHierarchy | None], None]


def _nobody(udid: str, app: AppHierarchy | None) -> None:
    return None


def _data_dir(udid: str) -> Path:
    return device_data_dir(udid, env=os.environ)


def as_hierarchy(app: SharedApp | None) -> AppHierarchy | None:
    """A shared app as the protocol tells a viewer of it."""
    if app is None:
        return None
    return {"name": app.name, "bundle_id": app.bundle_id, "sdk_version": app.sdk_version}


class AppHierarchyMerge:
    """The extra readers a device's snapshots merge in: the hierarchy the app in front shares, when a scope asks."""

    def __init__(
        self,
        *,
        copy: HostCopy | None = None,
        on_share: OnShare = _nobody,
        data_dir_for: Callable[[str], Path] = _data_dir,
        fetch: Fetch = fetch_hierarchy,
        find: Find = find_listings,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._copy = copy or HostCopy()
        self._on_share = on_share
        self._data_dir_for = data_dir_for
        self._fetch = fetch
        self._find = find
        self._clock = clock
        self._memory: dict[str, AppMemory] = {}

    def readers(self, udid: str, connector: str, config: SimConfig) -> Sequence[TreeReader]:
        """What else to read a device's screen with, under its scope's settings as they are now."""
        if not config.app_merge:
            memory = self._memory.pop(udid, None)
            if memory is not None and memory.shared is not None:
                self._on_share(udid, None)
            return ()
        memory = self._memory.setdefault(udid, AppMemory())
        reader = AppReader(
            udid,
            data_dir=self._data_dir_for(udid),
            memory=memory,
            max_nodes=config.app_max_nodes,
            timeout_s=config.app_timeout_ms / 1000,
            copy=self._copy,
            on_share=lambda app: self._on_share(udid, as_hierarchy(app)),
            fetch=self._fetch,
            find=self._find,
            clock=self._clock,
        )
        return (NamingReader(reader),) if config.app_name_unlabeled else (reader,)

    def forget(self, udid: str) -> None:
        """The device ended: what was remembered of its apps goes with it."""
        self._memory.pop(udid, None)

    async def close(self) -> None:
        self._memory.clear()
