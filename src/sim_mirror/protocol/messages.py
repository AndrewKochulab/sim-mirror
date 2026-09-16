# SPDX-License-Identifier: Apache-2.0
"""The screen socket's messages, built and read by the protocol's rules.

`protocol/v1/` defines the protocol once and `_generated.py` carries its constants and types. This is what a server
does with them: says hello, reads the client's hello, agrees on an encoding, and builds the events and frames it
sends. What a person does to the screen -- the client's input messages -- is read by `sim_mirror.core.screen_input`,
which also turns it into gestures.

A socket opens like this:

1. the server sends its `ServerHello`: the encodings, connector and capabilities it offers;
2. the client answers with its `ClientHello`: the encodings it decodes, most preferred first;
3. the server sends `StreamStart` with the first of those it offers -- or closes with `CLOSE_UNSUPPORTED` when there is
   none, and with `CLOSE_BAD_MESSAGE` when the answer is not a hello at all;
4. from then on: binary frames, each tagged by its first byte, and JSON events (`StatusEvent`, `AgentEvent`) out;
   `ClientInput` in, and anything else in ignored.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import cast

from sim_mirror._version import __version__
from sim_mirror.protocol._generated import (
    CLOSE_BAD_MESSAGE,
    CLOSE_UNSUPPORTED,
    ENCODINGS,
    MESSAGE_MAX_BYTES,
    PROTOCOL_VERSION,
    TAG_H264,
    TAG_JPEG,
    Agent,
    AgentDone,
    AgentIntent,
    AgentWorking,
    Capability,
    ClientHello,
    Device,
    Encoding,
    Point,
    ServerHello,
    StatusEvent,
    StreamStart,
)

#: How a server names itself in its hello.
SERVER = f"sim-mirror/{__version__}"
#: The first byte of a binary frame, by the encoding of the bytes after it.
TAGS: dict[Encoding, int] = {"jpeg": TAG_JPEG, "h264": TAG_H264}


class ProtocolError(Exception):
    """A client that did not keep to the protocol, with the close code and reason its socket is closed with."""

    def __init__(self, code: int, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


def parse(text: str) -> object | None:
    """A text message's JSON, or None when it is longer than a message may be or is not JSON."""
    if len(text.encode("utf-8")) > MESSAGE_MAX_BYTES:
        return None
    try:
        return cast(object, json.loads(text))
    except ValueError:
        return None


def server_hello(
    *,
    encodings: Sequence[Encoding],
    connector: str,
    capabilities: Iterable[Capability],
    fallback_reason: str | None = None,
) -> ServerHello:
    return {
        "type": "hello",
        "v": PROTOCOL_VERSION,
        "server": SERVER,
        "encodings": list(encodings),
        "connector": connector,
        "capabilities": list(dict.fromkeys(capabilities)),
        "fallback_reason": fallback_reason,
    }


def read_client_hello(message: object) -> ClientHello:
    """A client's hello, with the encodings this server knows kept in the client's order. Refuses with a close code."""
    if not isinstance(message, dict) or message.get("type") != "hello":
        raise ProtocolError(CLOSE_BAD_MESSAGE, "the first message must be a hello")
    version = message.get("v")
    if isinstance(version, bool) or version != PROTOCOL_VERSION:
        raise ProtocolError(
            CLOSE_UNSUPPORTED, f"protocol version {version!r} is not supported; this server speaks {PROTOCOL_VERSION}"
        )
    offered = message.get("encodings")
    if not isinstance(offered, list) or not offered or not all(isinstance(name, str) for name in offered):
        raise ProtocolError(CLOSE_BAD_MESSAGE, "a hello lists the encodings the client decodes")
    known = [cast(Encoding, name) for name in dict.fromkeys(offered) if name in ENCODINGS]
    if not known:
        raise ProtocolError(
            CLOSE_UNSUPPORTED, f"none of the encodings offered ({', '.join(offered)}) is one this server knows"
        )
    return {"type": "hello", "v": PROTOCOL_VERSION, "encodings": known}


def negotiate(offered: Sequence[Encoding], hello: ClientHello) -> Encoding:
    """The client's most preferred encoding that the server offers. Refuses with a close code when there is none."""
    for wanted in hello["encodings"]:
        if wanted in offered:
            return wanted
    raise ProtocolError(
        CLOSE_UNSUPPORTED,
        f"the client decodes {', '.join(hello['encodings'])}, and this server offers {', '.join(offered) or 'nothing'}",
    )


def stream_start(encoding: Encoding) -> StreamStart:
    return {"type": "stream", "encoding": encoding}


def status_event(device: Device) -> StatusEvent:
    return {"type": "status", **device}


def agent_intent(
    *,
    event_id: str,
    agent: Agent,
    kind: str,
    duration_ms: int,
    points: Sequence[Point] = (),
    pointer: bool = True,
    label: str = "",
    caption: str = "",
    lead_ms: int = 0,
    linger_ms: int = 0,
) -> AgentIntent:
    return {
        "type": "agent",
        "id": event_id,
        "phase": "intent",
        "agent": agent,
        "pointer": pointer,
        "label": label,
        "caption": caption,
        "lead_ms": lead_ms,
        "linger_ms": linger_ms,
        "gesture": {"kind": kind, "duration_ms": duration_ms, "points": list(points)},
    }


def agent_done(event_id: str, ok: bool) -> AgentDone:
    return {"type": "agent", "id": event_id, "phase": "done", "ok": ok}


def agent_working(event_id: str, agent: Agent, linger_ms: int) -> AgentWorking:
    return {"type": "agent", "id": event_id, "phase": "working", "agent": agent, "linger_ms": linger_ms}


def frame(encoding: Encoding, data: bytes) -> bytes:
    """A binary message: the encoding's tag, then the frame."""
    return bytes([TAGS[encoding]]) + data


def read_frame(message: bytes) -> tuple[Encoding, bytes] | None:
    """What a binary message holds -- its encoding and its frame -- or None for one that holds no frame."""
    if len(message) < 2:
        return None
    encoding = next((name for name, tag in TAGS.items() if tag == message[0]), None)
    return None if encoding is None else (encoding, message[1:])
