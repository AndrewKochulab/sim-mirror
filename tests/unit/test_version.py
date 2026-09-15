# SPDX-License-Identifier: Apache-2.0
import re

import sim_mirror


def test_version_is_semantic() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:[.-]?(?:a|b|rc|dev)\d+)?", sim_mirror.__version__)
