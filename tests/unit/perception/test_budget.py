# SPDX-License-Identifier: Apache-2.0
"""What an answer costs an agent, estimated: text by its length, an image by its pixels."""

from __future__ import annotations

import base64

from sim_mirror.perception.budget import estimate
from sim_mirror.perception.model import ElementNode, ScreenTree
from sim_mirror.testing.fakes import tiny_jpeg


def test_text_costs_about_a_token_per_four_characters() -> None:
    budget = estimate({"content": [{"type": "text", "text": "x" * 401}], "isError": False})
    assert (budget.text_chars, budget.text_tokens, budget.image_tokens, budget.images) == (401, 101, 0, 0)
    assert budget.line() == f"~101 tokens (estimate), {budget.size_bytes} bytes"


def test_an_image_costs_about_a_token_per_750_pixels_and_one_that_cannot_be_read_costs_nothing_counted() -> None:
    image = base64.b64encode(tiny_jpeg(400, 870)).decode()
    budget = estimate(
        {
            "content": [
                {"type": "text", "text": "1 frame"},
                {"type": "image", "data": image, "mimeType": "image/jpeg"},
                {"type": "image", "data": "not base64!", "mimeType": "image/jpeg"},
                {"type": "image", "data": base64.b64encode(b"png").decode()},
                "not a block",
                {"type": "audio"},
            ]
        }
    )
    assert budget.image_pixels == 400 * 870 and budget.image_tokens == 464 and budget.images == 3
    assert budget.tokens == 466 and budget.line().startswith("~466 tokens (estimate), 3 images, ")
    single = estimate({"content": [{"type": "image", "data": image}]})
    assert ", 1 image, " in single.line()
    assert estimate({}).tokens == 0


def test_a_tree_walks_its_roots_in_order() -> None:
    tree = ScreenTree(roots=(ElementNode(label="a", children=(ElementNode(label="b"),)), ElementNode(label="c")))
    assert [node.label for node in tree.walk()] == ["a", "b", "c"]
