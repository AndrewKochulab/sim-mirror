# SPDX-License-Identifier: Apache-2.0
"""Made-up screens as real JPEGs, and a device that shows a script of them."""

from __future__ import annotations

from io import BytesIO

from PIL import Image

from sim_mirror.testing.pictures import HEIGHT, WIDTH, PictureEngine, picture


def test_a_picture_is_a_jpeg_of_its_size_with_its_boxes_painted() -> None:
    with Image.open(BytesIO(picture(40, 20, background=200, boxes=[(0, 0, 10, 20, 0)]))) as image:
        assert (image.format, image.size) == ("JPEG", (40, 20))
        grey = image.convert("L")
        assert grey.getpixel((2, 10)) < 30 and grey.getpixel((30, 10)) > 170


async def test_a_picture_engine_shows_its_pictures_in_turn_and_holds_the_last() -> None:
    first, last = picture(boxes=[(0, 0, 5, 5, 0)]), picture()
    engine = PictureEngine([first, last])
    shots = [await engine.screenshot(max_width=160, quality=40) for _ in range(3)]
    assert [shot.jpeg for shot in shots] == [first, last, last]
    assert (shots[0].width, shots[0].height, engine.looks, len(engine.screenshots)) == (WIDTH, HEIGHT, 3, 3)


async def test_a_picture_engine_can_paint_each_look_and_read_its_own_document() -> None:
    engine = PictureEngine(lambda look: picture(boxes=[(look, 0, 4, 4, 0)]), document={"elements": []})
    assert (await engine.screenshot(max_width=160, quality=40)).jpeg == picture(boxes=[(0, 0, 4, 4, 0)])
    assert await engine.accessibility() == {"elements": []}
