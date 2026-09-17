# SPDX-License-Identifier: Apache-2.0
"""The native helper's wire protocol, from SimMirror's side.

Every message on the helper's socket is a frame, and every number in it is big-endian::

    u32 length     bytes after this field
    u8  kind       REQUEST, REPLY, FAILURE or CHUNK
    u32 id         the request this frame asks or answers
    u32 json size
    json           a UTF-8 JSON object; empty means {}
    blob           the rest: a JPEG, an access unit, or nothing

SimMirror sends requests (``{"op": "screenshot", ...}``); the helper answers each with a reply, a failure that says
why, or -- for a stream -- chunks until the connection closes. The helper's side is
``helper/Sources/HelperCore/Wire.swift``, and both are tested against the same vectors
(``helper/Tests/Fixtures/wire-vectors.json``).
"""

from __future__ import annotations

import asyncio
import json
import struct
from dataclasses import dataclass, field
from typing import Any

#: The protocol's version, which a hello must say.
VERSION = 1

REQUEST = 1
REPLY = 2
FAILURE = 3
CHUNK = 4
KINDS = frozenset({REQUEST, REPLY, FAILURE, CHUNK})

#: The largest frame either side accepts.
MAX_FRAME = 64 << 20
_HEADER = struct.Struct(">IBII")
#: The bytes a frame's length counts before its JSON: kind, id and JSON size.
_AFTER_LENGTH = _HEADER.size - 4


class WireError(Exception):
    """Bytes on the helper's socket that are not a frame."""


@dataclass(frozen=True)
class Frame:
    kind: int
    id: int
    json: bytes = b""
    blob: bytes = field(default=b"", repr=False)

    def document(self) -> dict[str, Any]:
        """The JSON object the frame carries; an empty one is {}."""
        if not self.json:
            return {}
        try:
            value: object = json.loads(self.json)
        except ValueError as exc:
            raise WireError(f"a frame whose JSON cannot be read: {exc}") from exc
        if not isinstance(value, dict):
            raise WireError("a frame whose JSON is not an object")
        return value


def encode(frame: Frame) -> bytes:
    return _HEADER.pack(_AFTER_LENGTH + len(frame.json) + len(frame.blob), frame.kind, frame.id, len(frame.json)) + (
        frame.json + frame.blob
    )


def request(request_id: int, op: str, **fields: Any) -> bytes:
    """A request frame asking for `op` with these fields."""
    body = json.dumps({"op": op, **fields}, separators=(",", ":"), sort_keys=True).encode()
    return encode(Frame(REQUEST, request_id, body))


async def read_frame(reader: asyncio.StreamReader) -> Frame | None:
    """The next frame, or None when the helper has closed the connection between frames."""
    try:
        header = await reader.readexactly(_HEADER.size)
    except asyncio.IncompleteReadError as exc:
        if not exc.partial:
            return None
        raise WireError("the helper closed the connection inside a frame") from exc
    length, kind, frame_id, json_size = _HEADER.unpack(header)
    if length < _AFTER_LENGTH or length > MAX_FRAME:
        raise WireError(f"a frame of {length} bytes")
    if kind not in KINDS:
        raise WireError(f"frame kind {kind} is not one SimMirror knows")
    if json_size > length - _AFTER_LENGTH:
        raise WireError(f"a frame's JSON of {json_size} bytes runs past its end")
    try:
        body = await reader.readexactly(length - _AFTER_LENGTH)
    except asyncio.IncompleteReadError as exc:
        raise WireError("the helper closed the connection inside a frame") from exc
    return Frame(kind, frame_id, body[:json_size], body[json_size:])
