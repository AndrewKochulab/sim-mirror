# SPDX-License-Identifier: Apache-2.0
"""Checking the values an agent's call names, with messages that say what would do."""

from __future__ import annotations

from typing import TypeGuard

#: The longest scheme, build configuration or test plan name a call or a setting may give.
XCODE_NAME_MAX = 128


class Invalid(ValueError):
    """A value a call named that cannot be used, said so the caller knows what to give instead."""


def is_number(value: object) -> bool:
    """A real number: an int or a float, never a bool."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_xcode_name(value: object) -> TypeGuard[str]:
    """A name Xcode lets a person give a scheme, a build configuration or a test plan -- `App (Staging)` as much as
    `Debug` -- that reaches xcodebuild as nothing but that name: one printable line, trimmed, and not an option.

    xcodebuild is run with an argument vector, never a shell, so punctuation means nothing to it; a leading `-` would,
    as the option after `-scheme`. Anything narrower refuses names Xcode made, and an agent told "it has App
    (Staging)" and then refused for using it can only try again.
    """
    return (
        isinstance(value, str)
        and 0 < len(value) <= XCODE_NAME_MAX
        and value.isprintable()
        and value == value.strip()
        and not value.startswith("-")
    )


def whole(value: object, default: int, bounds: tuple[int, int], name: str, unit: str | None = "milliseconds") -> int:
    """`value` as a whole number within `bounds`, `default` when it is None. Raises `Invalid` otherwise."""
    if value is None:
        return default
    low, high = bounds
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        of_unit = f" of {unit}" if unit else ""
        raise Invalid(f"{name} must be a whole number{of_unit} from {low} to {high}")
    return value
