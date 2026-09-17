# SPDX-License-Identifier: Apache-2.0
"""The socket's handshake, events and frames, built and read as the protocol says."""

from __future__ import annotations

import pytest

from sim_mirror import __version__
from sim_mirror.protocol import (
    CLOSE_BAD_MESSAGE,
    CLOSE_UNSUPPORTED,
    MESSAGE_MAX_BYTES,
    PROTOCOL_VERSION,
    SCREEN_TEXT_MAX_BOXES,
    TAG_H264,
    TAG_JPEG,
    ProtocolError,
    agent_done,
    agent_intent,
    frame,
    negotiate,
    parse,
    read_client_hello,
    read_frame,
    screen_text,
    server_hello,
    status_event,
    stream_start,
    text_box,
)
from sim_mirror.protocol._generated import Device


def test_the_server_says_who_it_is_and_what_it_offers_once_each() -> None:
    hello = server_hello(
        encodings=["h264", "jpeg"], connector="idb", capabilities=["screenshot", "input_touch", "screenshot"]
    )
    assert hello == {
        "type": "hello",
        "v": PROTOCOL_VERSION,
        "server": f"sim-mirror/{__version__}",
        "encodings": ["h264", "jpeg"],
        "connector": "idb",
        "capabilities": ["screenshot", "input_touch"],
        "fallback_reason": None,
    }
    fallback = server_hello(encodings=["jpeg"], connector="simctl", capabilities=[], fallback_reason="no companion")
    assert fallback["fallback_reason"] == "no companion"


def test_a_client_hello_keeps_the_encodings_this_server_knows_in_the_clients_order() -> None:
    hello = read_client_hello({"type": "hello", "v": 1, "encodings": ["webrtc", "h264", "jpeg", "h264"], "x": 1})
    assert hello == {"type": "hello", "v": 1, "encodings": ["h264", "jpeg"]}


@pytest.mark.parametrize(
    ("message", "code", "says"),
    [
        ("hello", CLOSE_BAD_MESSAGE, "must be a hello"),
        ({"type": "touch"}, CLOSE_BAD_MESSAGE, "must be a hello"),
        ({"type": "hello", "v": 2, "encodings": ["jpeg"]}, CLOSE_UNSUPPORTED, "version 2 is not supported"),
        ({"type": "hello", "v": True, "encodings": ["jpeg"]}, CLOSE_UNSUPPORTED, "version True"),
        ({"type": "hello", "encodings": ["jpeg"]}, CLOSE_UNSUPPORTED, "version None"),
        ({"type": "hello", "v": 1, "encodings": []}, CLOSE_BAD_MESSAGE, "lists the encodings"),
        ({"type": "hello", "v": 1, "encodings": "jpeg"}, CLOSE_BAD_MESSAGE, "lists the encodings"),
        ({"type": "hello", "v": 1, "encodings": [1]}, CLOSE_BAD_MESSAGE, "lists the encodings"),
        ({"type": "hello", "v": 1, "encodings": ["vp9"]}, CLOSE_UNSUPPORTED, "(vp9) is one this server knows"),
    ],
)
def test_a_hello_that_breaks_the_protocol_is_refused_with_its_close_code(message: object, code: int, says: str) -> None:
    with pytest.raises(ProtocolError) as refused:
        read_client_hello(message)
    assert refused.value.code == code and says in refused.value.reason and str(refused.value) == refused.value.reason


def test_the_encoding_is_the_clients_first_choice_the_server_offers() -> None:
    hello = read_client_hello({"type": "hello", "v": 1, "encodings": ["h264", "jpeg"]})
    assert negotiate(["jpeg", "h264"], hello) == "h264"
    assert negotiate(["jpeg"], hello) == "jpeg"
    with pytest.raises(ProtocolError) as refused:
        negotiate([], hello)
    assert refused.value.code == CLOSE_UNSUPPORTED and "offers nothing" in refused.value.reason
    only_h264 = read_client_hello({"type": "hello", "v": 1, "encodings": ["h264"]})
    with pytest.raises(ProtocolError, match="decodes h264, and this server offers jpeg"):
        negotiate(["jpeg"], only_h264)


def test_text_messages_are_parsed_within_the_size_a_message_may_be() -> None:
    assert parse('{"type": "key", "name": "return"}') == {"type": "key", "name": "return"}
    assert parse("not json") is None
    assert parse('"' + "é" * (MESSAGE_MAX_BYTES // 2) + '"') is None
    assert parse('"' + "a" * (MESSAGE_MAX_BYTES - 2) + '"') == "a" * (MESSAGE_MAX_BYTES - 2)


def test_events_carry_their_type_and_every_field() -> None:
    device: Device = {
        "udid": "U",
        "name": "iPhone 17 Pro",
        "runtime": "iOS 26.5",
        "state": "ready",
        "reason": None,
        "since_ms": 0,
        "viewers": 1,
        "busy": None,
        "created": False,
        "booted_by_us": True,
        "screen": None,
    }
    assert status_event(device) == {"type": "status", **device}
    assert stream_start("h264") == {"type": "stream", "encoding": "h264"}
    intent = agent_intent(
        event_id="a1",
        agent={"key": "k", "title": "Agent"},
        kind="tap",
        duration_ms=50,
        points=[(0.5, 0.25)],
        label="General",
        lead_ms=250,
    )
    assert intent["gesture"] == {"kind": "tap", "duration_ms": 50, "points": [(0.5, 0.25)]}
    assert (intent["pointer"], intent["caption"], intent["phase"]) == (True, "", "intent")
    assert agent_done("a1", False) == {"type": "agent", "id": "a1", "phase": "done", "ok": False}
    box = text_box("Sign in", 0.9, 0.1, 0.2, 0.3, 0.04)
    assert box == {"text": "Sign in", "confidence": 0.9, "x": 0.1, "y": 0.2, "w": 0.3, "h": 0.04}
    assert screen_text("t1", [box], hold_ms=500) == {"type": "screen_text", "id": "t1", "hold_ms": 500, "boxes": [box]}
    assert screen_text("t2") == {"type": "screen_text", "id": "t2", "hold_ms": 0, "boxes": []}
    assert len(screen_text("t3", [box] * (SCREEN_TEXT_MAX_BOXES + 1))["boxes"]) == SCREEN_TEXT_MAX_BOXES


def test_a_frame_is_its_tag_then_its_bytes_and_reads_back() -> None:
    assert frame("jpeg", b"\xff\xd8") == bytes([TAG_JPEG]) + b"\xff\xd8"
    assert read_frame(frame("h264", b"\x00\x00\x01\x67")) == ("h264", b"\x00\x00\x01\x67")
    assert read_frame(bytes([TAG_H264])) is None
    assert read_frame(b"\x09data") is None
