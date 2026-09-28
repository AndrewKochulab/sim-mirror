# SPDX-License-Identifier: Apache-2.0
"""What a device's own tools answer when they fail: one base for simctl's refusals and devicectl's."""

from __future__ import annotations

from typing import Any


class DeviceControlError(Exception):
    """A call on a device's own tool -- simctl for a simulator, devicectl for a real device -- that did not do what it
    was asked, said so a person can act on it. `result` is what the tool answered, when it ran."""

    def __init__(self, message: str, result: Any = None) -> None:
        super().__init__(message)
        self.result = result
