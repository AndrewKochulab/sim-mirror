# SPDX-License-Identifier: Apache-2.0
"""How a person's HTTP routes answer: ``{"ok": true, "data": …}``; a refusal is an HTTP error with the reason."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import HTTPException

from sim_mirror.core.runtime import Runtime

NOT_RUNNING = "The simulator service is not running."

#: How a router finds the runtime on each request; None while the host has none running.
RuntimeSource = Callable[[], Runtime | None]


def ok(data: object) -> dict[str, Any]:
    return {"ok": True, "data": data}


def running(source: RuntimeSource) -> Runtime:
    """The runtime, or a 503 saying there is none."""
    runtime = source()
    if runtime is None:
        raise HTTPException(503, NOT_RUNNING)
    return runtime
