# SPDX-License-Identifier: Apache-2.0
"""What a tool's answer costs an agent, estimated.

Models count tokens their own way, so these are estimates and always say so: text at about four characters a token,
and an image at about one token per 750 of its pixels. They are for comparing answers -- a snapshot against a
screenshot -- not for billing.
"""

from __future__ import annotations

import base64
import binascii
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sim_mirror.platform.images import jpeg_size

CHARS_PER_TOKEN = 4
PIXELS_PER_TOKEN = 750


@dataclass(frozen=True)
class Budget:
    text_chars: int
    image_pixels: int
    images: int
    #: The whole answer as JSON, as it goes over the wire.
    size_bytes: int

    @property
    def text_tokens(self) -> int:
        return math.ceil(self.text_chars / CHARS_PER_TOKEN)

    @property
    def image_tokens(self) -> int:
        return math.ceil(self.image_pixels / PIXELS_PER_TOKEN)

    @property
    def tokens(self) -> int:
        return self.text_tokens + self.image_tokens

    def line(self) -> str:
        images = f", {self.images} image{'s' if self.images != 1 else ''}" if self.images else ""
        return f"~{self.tokens} tokens (estimate){images}, {self.size_bytes} bytes"


def estimate(result: Mapping[str, Any]) -> Budget:
    """The estimated cost of an MCP tool result: its text blocks, and its JPEG images by their size."""
    chars = pixels = images = 0
    for block in result.get("content") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            chars += len(str(block.get("text", "")))
        elif block.get("type") == "image":
            images += 1
            try:
                size = jpeg_size(base64.b64decode(str(block.get("data", "")), validate=True))
            except (binascii.Error, ValueError):
                size = None
            if size is not None:
                pixels += size[0] * size[1]
    return Budget(chars, pixels, images, len(json.dumps(result).encode("utf-8")))
