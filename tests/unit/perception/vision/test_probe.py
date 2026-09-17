# SPDX-License-Identifier: Apache-2.0
"""The doctor's test picture: a JPEG of text a reader should read back."""

from __future__ import annotations

from io import BytesIO

from PIL import Image

from sim_mirror.perception.vision.probe import PROBE_SIZE, PROBE_TEXT, probe_picture


def test_the_test_picture_is_a_jpeg_of_dark_text_on_a_light_screen_with_room_around_it() -> None:
    with Image.open(BytesIO(probe_picture())) as image:
        assert image.format == "JPEG"
        width, height = image.size
        assert width > len(PROBE_TEXT) * PROBE_SIZE // 3 and height > 4 * PROBE_SIZE
        grey = image.convert("L")
        assert grey.getpixel((2, 2)) > 230 and min(grey.tobytes()) < 60
    assert probe_picture("Wider text than the default one") != probe_picture()
