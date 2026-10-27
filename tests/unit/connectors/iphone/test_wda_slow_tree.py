# SPDX-License-Identifier: Apache-2.0
"""A screen whose element tree WebDriverAgent builds slowly, read as a snapshot reads it: the first read falls to the
screen's pixels and says why, and the reads after it do not each wait out the tree still being built -- WebDriverAgent
serving one request at a time, they would each queue behind it and time out (field report B7: 12 in a row)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

import pytest

from sim_mirror.connectors.base import ConnectorError, Screen
from sim_mirror.connectors.iphone.wda_client import WdaClient
from sim_mirror.connectors.iphone.wda_roles import Orientation, WdaReader
from sim_mirror.perception.model import PIXELS, ElementNode, Frame, ScreenTree
from sim_mirror.perception.readers import DocumentReader, FallbackReader
from sim_mirror.testing.native import short_run_dir
from sim_mirror.testing.wda import FakeWda

SCREEN = Screen(1179, 2556, 393, 852, 3.0)
#: How long the fake takes to build the tree, how long a snapshot waits for it -- a long list on a device -- and how
#: long a probe of a busy WebDriverAgent waits for it to answer.
BUILD_S = 0.8
WAIT_S = 0.25
PROBE_S = 0.1


class Pixels:
    """The screen's pixels, read from a screenshot that does not go through WebDriverAgent."""

    def __init__(self) -> None:
        self.reads = 0

    async def read(self) -> ScreenTree:
        self.reads += 1
        line = ElementNode(role="StaticText", label="Activity", frame=Frame(16, 100, 120, 20), source=PIXELS)
        return ScreenTree(roots=(line,), pixels=True)


@pytest.fixture
async def wda() -> AsyncIterator[FakeWda]:
    with short_run_dir() as folder:
        fake = await FakeWda(folder).serve()
        try:
            yield fake
        finally:
            await fake.stop()


def sources(wda: FakeWda) -> int:
    return sum(1 for _method, path, _body in wda.requests if "/source" in path)


async def test_a_slow_tree_falls_to_pixels_once_and_the_reads_after_it_do_not_queue_behind_it(wda: FakeWda) -> None:
    client = WdaClient(wda.opener(), probe_timeout_s=PROBE_S)
    pixels = Pixels()
    tree = DocumentReader(
        WdaReader(client, Orientation(client, SCREEN), SCREEN, timeout_s=WAIT_S, cold_timeout_s=WAIT_S), "wda"
    )
    snapshot = FallbackReader(tree, pixels, screen=SCREEN)
    wda.source_delay_s = BUILD_S

    first = await snapshot.read()
    assert first.pixels and pixels.reads == 1
    assert first.notes[0].startswith(
        "accessibility could not be read: WebDriverAgent did not build the element tree within 0.25 s"
    )

    # While WebDriverAgent is still building it, each read goes straight to the pixels: no tree is asked for again,
    # and none waits for one.
    for _ in range(3):
        started = time.monotonic()
        busy = await snapshot.read()
        assert time.monotonic() - started < WAIT_S
        assert busy.pixels and "the element tree is still being built on the device" in busy.notes[0]
    assert sources(wda) == 1 and pixels.reads == 4

    # Once it is done, the tree is read again -- quickly now.
    await asyncio.sleep(BUILD_S)
    wda.source_delay_s = 0
    await asyncio.sleep(PROBE_S * 1.5)
    read = await snapshot.read()
    assert not read.pixels and read.notes == () and pixels.reads == 4
    assert [node.label for node in read.walk()] == ["Notes", "Title", "Save"]
    assert sources(wda) == 2
    served = [path for _method, path in wda.served]
    assert served.index("/source") < served.index("/status"), "the tree given up on was built to its end first"


async def test_a_slow_tree_with_its_pixels_unreadable_too_says_both(wda: FakeWda) -> None:
    class Broken:
        async def read(self) -> ScreenTree:
            raise ConnectorError("the text reader did not answer a reading of the screen within 5 seconds")

    client = WdaClient(wda.opener(), probe_timeout_s=PROBE_S)
    tree = DocumentReader(
        WdaReader(client, Orientation(client, SCREEN), SCREEN, timeout_s=WAIT_S, cold_timeout_s=WAIT_S), "wda"
    )
    wda.source_delay_s = BUILD_S
    with pytest.raises(ConnectorError) as failed:
        await FallbackReader(tree, Broken(), screen=SCREEN).read()
    assert "did not build the element tree within 0.25 s" in str(failed.value)
    assert str(failed.value).endswith(
        "the screen's pixels could not be read either: the text reader did not answer a reading of the screen within "
        "5 seconds"
    )
