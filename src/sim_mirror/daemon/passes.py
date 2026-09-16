# SPDX-License-Identifier: Apache-2.0
"""How a page gets in without a token in its URL: a one-shot code, spent for a viewer token held only in memory.

``sim-mirror open`` asks the daemon, with the admin token, for a code for a scope and opens
``/viewer/<scope>#code=…``; a host's backend asks for an embed ticket and frames ``/embed/<scope>#ticket=…``. Either
is a `OneShotCodes` code: spent once, within `CODE_TTL_S`. It sits in the URL's fragment, which a browser never sends
to a server, so it reaches no log; the page posts it to ``/api/v1/auth/exchange`` and gets a viewer token for that
scope (`ViewerSessions`) that lives in the page's memory and the daemon's, and nowhere on disk.

A code remembers what kind of session it opens, and so does the session: a ``viewer`` (``sim-mirror open``), an
``embed`` (a host's frame), or ``settings`` (``sim-mirror open --settings``), the one kind that may change settings --
for `SETTINGS_TTL_S`, not the twelve hours a viewer stays open.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from typing import Literal, NamedTuple

CODE_TTL_S = 60.0
VIEWER_TTL_S = 12 * 3600.0
SETTINGS_TTL_S = 3600.0

SessionKind = Literal["viewer", "embed", "settings"]


class Pass(NamedTuple):
    """What a code or a session is for: a scope, and the kind of session."""

    scope_id: str
    kind: SessionKind


def ttl_of(kind: SessionKind) -> float:
    return SETTINGS_TTL_S if kind == "settings" else VIEWER_TTL_S


class OneShotCodes:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic, ttl_s: float = CODE_TTL_S) -> None:
        self._clock = clock
        self._ttl_s = ttl_s
        self._codes: dict[str, tuple[float, Pass]] = {}

    def mint(self, scope_id: str, kind: SessionKind = "viewer") -> str:
        now = self._clock()
        self._codes = {code: entry for code, entry in self._codes.items() if entry[0] > now}
        code = secrets.token_urlsafe(24)
        self._codes[code] = (now + self._ttl_s, Pass(scope_id, kind))
        return code

    def redeem(self, code: str) -> Pass | None:
        """What a code opens, spending it -- or None for one that was never made, is spent or has expired."""
        entry = self._codes.pop(code, None)
        if entry is None or entry[0] <= self._clock():
            return None
        return entry[1]


class ViewerSessions:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._tokens: dict[str, tuple[float, Pass]] = {}

    def open(self, scope_id: str, kind: SessionKind = "viewer") -> str:
        now = self._clock()
        self._tokens = {token: entry for token, entry in self._tokens.items() if entry[0] > now}
        token = secrets.token_urlsafe(32)
        self._tokens[token] = (now + ttl_of(kind), Pass(scope_id, kind))
        return token

    def session(self, token: str | None) -> Pass | None:
        """The scope and kind a viewer token is for, or None."""
        entry = self._tokens.get(token) if token else None
        if entry is None or entry[0] <= self._clock():
            return None
        return entry[1]

    def scope_of(self, token: str | None) -> str | None:
        """The scope a viewer token is for, or None."""
        found = self.session(token)
        return None if found is None else found.scope_id
