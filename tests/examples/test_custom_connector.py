# SPDX-License-Identifier: Apache-2.0
"""The custom-connector example's own tests, run with SimMirror's suite so the example keeps the contract."""

from __future__ import annotations

from test_recorded_connector import (  # noqa: F401 -- collected and run here
    test_a_folder_of_frames_keeps_the_contract,
    test_a_jpeg_is_read_by_its_frame_header_and_anything_else_is_not_one,
    test_frames_play_in_name_order_and_start_over,
    test_the_entry_point_makes_the_connector,
    test_without_frames_it_says_why_and_attaches_to_nothing,
)
