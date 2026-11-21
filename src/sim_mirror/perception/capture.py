# SPDX-License-Identifier: Apache-2.0
"""The parts of a frozen device frame a person can pick, in frame-relative coordinates."""

from __future__ import annotations

from sim_mirror.connectors.base import Crop
from sim_mirror.perception.model import PIXELS, ElementNode, ScreenTree
from sim_mirror.perception.orientation import Interface
from sim_mirror.protocol import CaptureView


def views(tree: ScreenTree, interface: Interface) -> list[CaptureView]:
    result: list[CaptureView] = []
    width, height = interface.device.width_pt, interface.device.height_pt
    pending: list[tuple[ElementNode, int | None]] = [(node, None) for node in reversed(tree.roots)]
    while pending and len(result) < 800:
        node, parent = pending.pop()
        frame = node.frame
        if frame and node.source != PIXELS and frame.width > 0 and frame.height > 0:
            box = interface.crop(Crop(frame.x, frame.y, frame.width, frame.height))
            left, top = max(0.0, box.x / width), max(0.0, box.y / height)
            right, bottom = min(1.0, (box.x + box.width) / width), min(1.0, (box.y + box.height) / height)
            if left < right and top < bottom:
                result.append(
                    CaptureView(
                        label=(node.label or node.role or "View")[:100],
                        parent=parent,
                        x=left,
                        y=top,
                        w=right - left,
                        h=bottom - top,
                    )
                )
                parent = len(result) - 1
        pending.extend((child, parent) for child in reversed(node.children))
    return result
