# SPDX-License-Identifier: Apache-2.0
"""Settings from the environment: ``SIM_MIRROR_<PATH>``, such as ``SIM_MIRROR_STREAM_FPS=60``.

Each variable is parsed by its setting's rule -- ``true``/``false`` for a flag, a whole number, a comma-separated list
of origins -- and checked like any other value. A variable that does not parse or is refused is reported and ignored.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sim_mirror.config.schema import SETTINGS


def from_env(env: Mapping[str, str]) -> tuple[dict[str, Any], list[str]]:
    """The settings the environment sets, by flat key, and what is wrong with the variables that could not be used."""
    values: dict[str, Any] = {}
    problems: list[str] = []
    for setting in SETTINGS:
        raw = env.get(setting.env)
        if raw is None:
            continue
        try:
            value = setting.rule.parse(raw)
        except ValueError as exc:
            problems.append(f"{setting.env} {exc}")
            continue
        refused = setting.errors(value, name=setting.env)
        if refused:
            problems.extend(refused)
            continue
        values[setting.key] = value
    return values, problems
