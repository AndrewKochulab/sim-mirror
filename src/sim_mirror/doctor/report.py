# SPDX-License-Identifier: Apache-2.0
"""What the doctor found: one result per check, as lines a person reads or JSON a compatibility report carries.

A check is ``ok``, ``warn`` (SimMirror works, with less), ``fail`` (it does not work until fixed) or ``skip`` (not
checked, and why). The command exits 1 when anything failed, 2 when something only warned, and 0 otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from sim_mirror._version import __version__
from sim_mirror.protocol import PROTOCOL_VERSION

Status = Literal["ok", "warn", "fail", "skip"]
MARKS: dict[Status, str] = {"ok": "ok  ", "warn": "warn", "fail": "FAIL", "skip": "skip"}


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    detail: str
    #: What a person does about it; empty when there is nothing to do.
    fix: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status, "detail": self.detail, "fix": self.fix}


@dataclass(frozen=True)
class Report:
    results: tuple[CheckResult, ...]

    @property
    def status(self) -> Status:
        statuses = {result.status for result in self.results}
        return "fail" if "fail" in statuses else "warn" if "warn" in statuses else "ok"

    @property
    def exit_code(self) -> int:
        return {"fail": 1, "warn": 2}.get(self.status, 0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sim_mirror": __version__,
            "protocol": PROTOCOL_VERSION,
            "status": self.status,
            "checks": [result.to_dict() for result in self.results],
        }

    def text(self) -> str:
        lines: list[str] = []
        for result in self.results:
            lines.append(f"{MARKS[result.status]}  {result.name}: {result.detail}")
            if result.fix:
                lines.append(f"      fix: {result.fix}")
        return "\n".join(lines)
