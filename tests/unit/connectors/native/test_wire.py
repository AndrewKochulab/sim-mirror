# SPDX-License-Identifier: Apache-2.0
"""The native helper's wire protocol, held to the same vectors the Swift helper is tested against."""

from __future__ import annotations

import asyncio
import json
import struct
from pathlib import Path
from typing import Any

import pytest

from sim_mirror.connectors.native import wire

VECTORS = Path(__file__).resolve().parents[4] / "helper" / "Tests" / "Fixtures" / "wire-vectors.json"


def vectors() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(VECTORS.read_text(encoding="utf-8"))
    return loaded


def reader_of(data: bytes, *, eof: bool = True) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    if eof:
        reader.feed_eof()
    return reader


async def test_every_shared_vector_encodes_and_reads_as_the_same_frame() -> None:
    shared = vectors()
    assert shared["wire"] == wire.VERSION
    for vector in shared["frames"]:
        frame = wire.Frame(vector["kind"], vector["id"], vector["json"].encode(), bytes.fromhex(vector["blob"]))
        assert wire.encode(frame).hex() == vector["hex"], vector["name"]
        reader = reader_of(bytes.fromhex(vector["hex"]))
        assert await wire.read_frame(reader) == frame, vector["name"]
        assert await wire.read_frame(reader) is None


async def test_a_request_is_its_op_and_fields_with_sorted_keys() -> None:
    hello = next(vector for vector in vectors()["frames"] if vector["name"] == "a hello")
    assert wire.request(1, "hello").hex() == hello["hex"]
    stream = next(vector for vector in vectors()["frames"] if vector["name"] == "a stream")
    assert wire.request(4, "stream", fps=30, scale=0.75, key_frame_s=1, bitrate=3_000_000).hex() == stream["hex"]


def test_a_frames_document_is_its_json_object() -> None:
    assert wire.Frame(wire.REPLY, 1).document() == {}
    assert wire.Frame(wire.REPLY, 1, b'{"a":1}').document() == {"a": 1}
    with pytest.raises(wire.WireError, match="cannot be read"):
        wire.Frame(wire.REPLY, 1, b"{").document()
    with pytest.raises(wire.WireError, match="not an object"):
        wire.Frame(wire.REPLY, 1, b"[1]").document()


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (struct.pack(">IBII", 2, 2, 1, 0), "a frame of 2 bytes"),
        (struct.pack(">IBII", wire.MAX_FRAME + 1, 2, 1, 0), "a frame of"),
        (struct.pack(">IBII", 9, 7, 1, 0), "frame kind 7"),
        (struct.pack(">IBII", 9, 2, 1, 5), "JSON of 5 bytes runs past its end"),
        (struct.pack(">IBII", 20, 2, 1, 0) + b"short", "inside a frame"),
        (b"\x00\x00\x00", "inside a frame"),
    ],
)
async def test_what_is_not_a_frame_is_refused(data: bytes, message: str) -> None:
    with pytest.raises(wire.WireError, match=message):
        await wire.read_frame(reader_of(data))
