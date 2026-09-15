# SPDX-License-Identifier: Apache-2.0
"""The repository's check scripts are plain files in scripts/, imported by their tests from there."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
