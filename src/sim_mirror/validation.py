# SPDX-License-Identifier: Apache-2.0
"""Checking the values an agent's call names, with messages that say what would do."""

from __future__ import annotations


class Invalid(ValueError):
    """A value a call named that cannot be used, said so the caller knows what to give instead."""


def is_number(value: object) -> bool:
    """A real number: an int or a float, never a bool."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def whole(value: object, default: int, bounds: tuple[int, int], name: str, unit: str | None = "milliseconds") -> int:
    """`value` as a whole number within `bounds`, `default` when it is None. Raises `Invalid` otherwise."""
    if value is None:
        return default
    low, high = bounds
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        of_unit = f" of {unit}" if unit else ""
        raise Invalid(f"{name} must be a whole number{of_unit} from {low} to {high}")
    return value
