# SPDX-License-Identifier: Apache-2.0
"""The screen socket protocol: its constants, its message types, and how messages are built and read.

The protocol is defined once in `protocol/v1/` at the repository's root (see its README); the constants and types are
generated from there into `_generated.py`, and hosts import them from here.
"""

from sim_mirror.protocol._generated import *  # noqa: F403 -- the generated surface is the public one
from sim_mirror.protocol.messages import (
    SERVER,
    TAGS,
    ProtocolError,
    agent_done,
    agent_intent,
    agent_working,
    frame,
    negotiate,
    parse,
    read_client_hello,
    read_frame,
    server_hello,
    status_event,
    stream_start,
)

__all__ = [
    "SERVER",
    "TAGS",
    "ProtocolError",
    "agent_done",
    "agent_intent",
    "agent_working",
    "frame",
    "negotiate",
    "parse",
    "read_client_hello",
    "read_frame",
    "server_hello",
    "status_event",
    "stream_start",
]
