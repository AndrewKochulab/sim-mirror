# SPDX-License-Identifier: Apache-2.0
"""A system alert over a turned app -- asking for the location, for notifications -- read as it is drawn.

The alert is the system's, not the app's: accessibility reads it as the frontmost application, a root with no name,
and its frames are always the screen's own portrait points, whichever way the device is turned and the alert drawn.
Measured on iOS 26.5 with the device turned as Simulator.app turns it, through idb_companion and the native helper
alike: Maps asking for the location and an app asking for notifications on an iPhone 17 Pro, drawn landscape right its
title along the screen's right edge at (329.7, 307) 42x260 -- standing, as its text does on the screen -- and on an iPad
Pro 13-inch, landscape either way and upside down. The root itself was sometimes the screen's shape and sometimes the
interface's, so the way the reader found from it (`orientation`) told nothing; touches at those frames' middles landed.

An alert is laid out the same way whatever the way: its buttons wider than they are tall, and its elements in reading
order -- the title, the message, then the buttons, top to bottom and side by side left to right. So when the frames,
read as they came, do not lie that way, the one way of the four that, read as the screen's points, lays them out so is
the way the alert is drawn (`as_drawn`), and its frames are put in that interface's points. A touch then lands where it
did, and the snapshot, the screenshot and the text read from its pixels are the alert as it is drawn. When no one way
does, the tree is left as it was read.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from itertools import pairwise

from sim_mirror.connectors.base import Crop, Screen
from sim_mirror.perception.model import ElementNode, Frame, ScreenTree
from sim_mirror.perception.orientation import ORIENTATIONS, Interface

#: The most elements an alert has: a title, a message, a field or two, and its buttons. More is no alert.
MOST_ELEMENTS = 12
#: How far apart, in points, two elements' middles may be across or down and still be in the same row.
ROW_PT = 2.0
#: How far, in points, an element may reach past the interface and still be on it.
EDGE_PT = 2.0


def _leaves(node: ElementNode) -> list[ElementNode]:
    return [leaf for child in node.children for leaf in _leaves(child)] if node.children else [node]


def _laid_out(frames: Sequence[tuple[str, Frame]], screen: Screen) -> bool:
    """Whether elements lie as an alert's do on an interface of this size: inside it, every button wider than tall,
    and each one after the one before it in reading order."""
    width, height = float(screen.width_pt), float(screen.height_pt)
    for role, frame in frames:
        inside = min(frame.x, frame.y) >= -EDGE_PT
        inside = inside and frame.x + frame.width <= width + EDGE_PT and frame.y + frame.height <= height + EDGE_PT
        if not inside or (role == "Button" and frame.width <= frame.height):
            return False
    middles = [(frame.x + frame.width / 2, frame.y + frame.height / 2) for _role, frame in frames]
    return all(
        y > before_y + ROW_PT or (abs(y - before_y) <= ROW_PT and x > before_x)
        for (before_x, before_y), (x, y) in pairwise(middles)
    )


def _in(interface: Interface, frame: Frame) -> Frame:
    region = interface.region(Crop(frame.x, frame.y, frame.width, frame.height))
    return Frame(region.x, region.y, region.width, region.height)


def _moved(node: ElementNode, interface: Interface) -> ElementNode:
    children = tuple(_moved(child, interface) for child in node.children)
    return replace(node, frame=None if node.frame is None else _in(interface, node.frame), children=children)


def as_drawn(tree: ScreenTree, screen: Screen) -> ScreenTree:
    """The tree, a system alert's frames in the points of the interface it is drawn in, turned as that is; as it is
    when it is no system alert, or one already read as it is drawn, or no one way lays it out."""
    if len(tree.roots) != 1 or tree.roots[0].role != "Application" or tree.roots[0].label.strip():
        return tree
    root = tree.roots[0]
    leaves = [(leaf.role, leaf.frame) for leaf in _leaves(root) if leaf is not root and leaf.frame is not None]
    if not 2 <= len(leaves) <= MOST_ELEMENTS or all(role != "Button" for role, _frame in leaves):
        return tree
    if _laid_out(leaves, Interface(screen, tree.orientation).screen):
        return tree
    interfaces = [Interface(screen, way) for way in ORIENTATIONS]
    ways = [it for it in interfaces if _laid_out([(role, _in(it, frame)) for role, frame in leaves], it.screen)]
    if len(ways) != 1:
        return tree
    [interface] = ways
    size = interface.screen
    whole = Frame(0.0, 0.0, float(size.width_pt), float(size.height_pt))
    children = tuple(_moved(child, interface) for child in root.children)
    return replace(tree, roots=(replace(root, frame=whole, children=children),), orientation=interface.orientation)
