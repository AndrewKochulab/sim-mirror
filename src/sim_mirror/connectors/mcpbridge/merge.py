# SPDX-License-Identifier: Apache-2.0
"""Xcode's UI hierarchy merged into the snapshots of a device another connector drives.

idb_companion's accessibility document leaves out what a web page shows, a widget's text and the status bar. Xcode 27's
hierarchy has them. With ``connectors.mcpbridge.merge`` on, a snapshot reads both: idb's tree whole, and from Xcode's
only what idb did not already say (`perception.readers.MergedReader`). Measured on iOS 27.0 (2026-09-16): Safari
showing example.com went from 5 elements to 8 -- its heading, its text and its link -- and the home screen from 13 to
19; Settings, a SwiftUI form, an alert and the keyboard were unchanged.

A reader is kept per device, opened on its first snapshot and closed when the device ends, the setting is turned off,
or the scope's Xcode changes. It is read on every snapshot, so the setting is read then too.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Sequence

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.mcpbridge.connector import NAME
from sim_mirror.connectors.mcpbridge.reader import BridgeReader
from sim_mirror.host_copy import HostCopy
from sim_mirror.perception.readers import DocumentReader, TreeReader

ReaderFor = Callable[[str, str], BridgeReader]


class HierarchyMerge:
    """The extra readers a device's snapshots merge in: Xcode's hierarchy, when a scope asks for it."""

    def __init__(self, *, copy: HostCopy | None = None, reader_for: ReaderFor | None = None) -> None:
        self._copy = copy or HostCopy()
        self._reader_for = reader_for or self._reader
        self._readers: dict[str, BridgeReader] = {}
        self._closing: set[asyncio.Task[None]] = set()

    def _reader(self, udid: str, developer_dir: str) -> BridgeReader:
        return BridgeReader(udid, developer_dir, copy=self._copy)

    def readers(self, udid: str, connector: str, config: SimConfig) -> Sequence[TreeReader]:
        """What else to read a device's screen with, under its scope's settings as they are now."""
        if connector == NAME or not config.mcpbridge_merge:
            self.forget(udid)
            return ()
        reader = self._readers.get(udid)
        if reader is not None and reader.developer_dir != config.developer_dir:
            self.forget(udid)
            reader = None
        if reader is None:
            reader = self._readers[udid] = self._reader_for(udid, config.developer_dir)
        return (DocumentReader(reader, NAME),)

    def forget(self, udid: str) -> None:
        """Let go of a device's reader: its session in Xcode is ended in the background."""
        reader = self._readers.pop(udid, None)
        if reader is not None:
            task = asyncio.get_running_loop().create_task(reader.close())
            self._closing.add(task)
            task.add_done_callback(self._closing.discard)

    async def close(self) -> None:
        """Let go of every reader, and wait until each has."""
        for udid in list(self._readers):
            self.forget(udid)
        for task in list(self._closing):
            with contextlib.suppress(Exception):
                await task
