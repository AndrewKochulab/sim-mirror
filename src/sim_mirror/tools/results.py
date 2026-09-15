# SPDX-License-Identifier: Apache-2.0
"""A tool's answer, shaped as MCP's: content blocks, and whether it is an error.

Text first: an image is sent only when one was asked for, because an image costs an agent far more than the lines that
say what is on the screen (`perception.snapshot`).
"""

from __future__ import annotations

import base64
from collections.abc import Sequence
from typing import Any

Result = dict[str, Any]


class ToolRefused(Exception):
    """A call a tool will not make, said so the agent knows what to do instead."""


def text(value: str, *, error: bool = False) -> Result:
    return {"content": [{"type": "text", "text": value}], "isError": error}


def images(caption: str, jpegs: Sequence[bytes]) -> Result:
    """A caption, then each JPEG."""
    blocks = [
        {"type": "image", "data": base64.b64encode(jpeg).decode("ascii"), "mimeType": "image/jpeg"} for jpeg in jpegs
    ]
    return {"content": [{"type": "text", "text": caption}, *blocks], "isError": False}
