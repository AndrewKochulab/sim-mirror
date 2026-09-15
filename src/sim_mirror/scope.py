# SPDX-License-Identifier: Apache-2.0
"""A scope: what devices, settings and agents are grouped by.

SimMirror knows nothing about the application embedding it. A standalone install makes one scope per project folder
(`Scope.for_folder`); a host application makes one for whatever it groups work by and hands SimMirror a `Scope`. A
device is kept per scope (``device.mode = "per_scope"``) or one per group (``"shared"``).

A scope's id is safe in a URL path segment and a file name: letters, digits and ``_ . : -``, starting with a letter or
digit, at most 128 characters.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

ID_PATTERN = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
LABEL_MAX = 200
#: The group every standalone scope belongs to: in ``shared`` device mode, all projects on the Mac share one device.
STANDALONE_GROUP = "local"


class InvalidScope(ValueError):
    """A scope id, group or label that cannot be used."""


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip("-._")
    return cleaned[:48] or "project"


def _one_line(text: str) -> str:
    return " ".join(text.split())[:LABEL_MAX]


@dataclass(frozen=True)
class Scope:
    id: str
    group: str
    label: str

    def __post_init__(self) -> None:
        for name in ("id", "group"):
            value = getattr(self, name)
            if not isinstance(value, str) or not ID_PATTERN.match(value):
                raise InvalidScope(
                    f"a scope {name} is 1 to 128 letters, digits, _ . : or -, starting with a letter or digit, "
                    f"not {value!r}"
                )
        if not isinstance(self.label, str) or len(self.label) > LABEL_MAX or any(c in self.label for c in "\n\r\x00"):
            raise InvalidScope(f"a scope label is one line of at most {LABEL_MAX} characters")

    @classmethod
    def named(cls, scope_id: str, *, group: str = STANDALONE_GROUP) -> Scope:
        """A scope a person named, such as ``sim-mirror open --scope demo``: its id is its label."""
        return cls(id=scope_id, group=group, label=scope_id)

    @classmethod
    def for_folder(cls, folder: Path) -> Scope:
        """A project folder's scope: its name and a short hash of its real path, so two folders with the same name
        are two scopes and a folder reached through a link is the same one."""
        real = folder.resolve()
        digest = hashlib.blake2b(str(real).encode("utf-8"), digest_size=4).hexdigest()
        name = real.name or "root"
        return cls(id=f"project-{_slug(name)}-{digest}", group=STANDALONE_GROUP, label=_one_line(name))
