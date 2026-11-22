# SPDX-License-Identifier: Apache-2.0
"""Only visible device views are selectable in a frozen frame."""

from sim_mirror.connectors.base import Screen
from sim_mirror.perception.capture import views
from sim_mirror.perception.model import PIXELS, ElementNode, Frame, ScreenTree
from sim_mirror.perception.orientation import Interface


def test_visible_children_keep_the_nearest_selectable_parent() -> None:
    interface = Interface(Screen(200, 400, 100, 200, 2))
    button = ElementNode(role="Button", frame=Frame(-10, 50, 60, 50))
    outside = ElementNode(frame=Frame(100, 150, 10, 10), children=(button,))
    text = ElementNode(label="Pixel text", source=PIXELS, frame=Frame(0, 0, 5, 10))
    container = ElementNode(label="Panel", frame=Frame(0, 0, 100, 200), children=(text, outside))
    assert views(ScreenTree(roots=(container,)), interface) == [
        {"label": "Panel", "parent": None, "x": 0, "y": 0, "w": 1, "h": 1},
        {"label": "Button", "parent": 0, "x": 0, "y": 0.25, "w": 0.5, "h": 0.25},
    ]


def test_a_large_tree_keeps_the_first_800_selectable_views_in_order() -> None:
    nodes = tuple(ElementNode(label=str(i), frame=Frame(0, 0, 10, 10)) for i in range(801))
    result = views(ScreenTree(roots=nodes), Interface(Screen(100, 100, 100, 100, 1)))
    assert [view["label"] for view in result] == [str(i) for i in range(800)]
