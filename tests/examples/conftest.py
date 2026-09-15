# SPDX-License-Identifier: Apache-2.0
"""The examples are plain folders to copy, not packages: their modules are imported from where they are."""

from __future__ import annotations

import sys
from pathlib import Path

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
for folder in ("embed-host", "fastapi-embed", "custom-connector"):
    path = str(EXAMPLES / folder)
    if path not in sys.path:
        sys.path.insert(0, path)
