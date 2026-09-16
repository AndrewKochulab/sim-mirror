# SPDX-License-Identifier: Apache-2.0
"""Images without an image library: a JPEG's size read from its bytes, and a JPEG shrunk with macOS's own ``sips``."""

from __future__ import annotations

import tempfile
from pathlib import Path

from sim_mirror.platform import process
from sim_mirror.platform.process import Runner

#: The markers that start a JPEG frame header, which says the image's size.
_START_OF_FRAME = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}
#: Markers that stand alone, with no length after them.
_STANDALONE = frozenset({0x01, *range(0xD0, 0xD8)})


def jpeg_size(data: bytes) -> tuple[int, int] | None:
    """A JPEG's width and height, read from its frame header; None for anything that is not a JPEG."""
    if not data.startswith(b"\xff\xd8"):
        return None
    index = 2
    while index + 4 <= len(data):
        if data[index] != 0xFF:
            return None
        marker = data[index + 1]
        if marker in _STANDALONE or marker == 0xFF:
            index += 1 if marker == 0xFF else 2
            continue
        length = int.from_bytes(data[index + 2 : index + 4], "big")
        if marker in _START_OF_FRAME:
            if index + 9 > len(data):
                return None
            height = int.from_bytes(data[index + 5 : index + 7], "big")
            width = int.from_bytes(data[index + 7 : index + 9], "big")
            return width, height
        index += 2 + length
    return None


async def resize_jpeg(data: bytes, *, max_width: int, quality: int, run: Runner = process.run) -> bytes | None:
    """The JPEG no wider than `max_width` and encoded at `quality`; None when sips could not make it."""
    with tempfile.TemporaryDirectory(prefix="sim-mirror-") as folder:
        source, target = Path(folder) / "in.jpg", Path(folder) / "out.jpg"
        source.write_bytes(data)
        argv = ("sips", "--resampleWidth", str(max_width), "-s", "format", "jpeg", "-s", "formatOptions",
                str(quality), str(source), "--out", str(target))  # fmt: skip
        code, _out = await run(argv)
        if code != 0 or not target.is_file():
            return None
        return target.read_bytes()
