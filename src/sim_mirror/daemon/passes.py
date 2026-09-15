# SPDX-License-Identifier: Apache-2.0
"""How a page gets in without a token in its URL: a one-shot code, spent for a viewer token held only in memory.

``sim-mirror open`` asks the daemon, with the admin token, for a code for a scope and opens
``/viewer/<scope>#code=…``; a host's backend asks for an embed ticket and frames ``/embed/<scope>#ticket=…``. Either
is a `OneShotCodes` code: spent once, within `CODE_TTL_S`. It sits in the URL's fragment, which a browser never sends
to a server, so it reaches no log; the page posts it to ``/api/v1/auth/exchange`` and gets a viewer token for that
scope (`ViewerSessions`) that lives in the page's memory and the daemon's, and nowhere on disk.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable

CODE_TTL_S = 60.0
VIEWER_TTL_S = 12 * 3600.0


class OneShotCodes:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic, ttl_s: float = CODE_TTL_S) -> None:
        self._clock = clock
        self._ttl_s = ttl_s
        self._codes: dict[str, tuple[float, str]] = {}

    def mint(self, scope_id: str) -> str:
        now = self._clock()
        self._codes = {code: entry for code, entry in self._codes.items() if entry[0] > now}
        code = secrets.token_urlsafe(24)
        self._codes[code] = (now + self._ttl_s, scope_id)
        return code

    def redeem(self, code: str) -> str | None:
        """The scope a code opens, spending it -- or None for one that was never made, is spent or has expired."""
        entry = self._codes.pop(code, None)
        if entry is None or entry[0] <= self._clock():
            return None
        return entry[1]


class ViewerSessions:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic, ttl_s: float = VIEWER_TTL_S) -> None:
        self._clock = clock
        self._ttl_s = ttl_s
        self._tokens: dict[str, tuple[float, str]] = {}

    def open(self, scope_id: str) -> str:
        now = self._clock()
        self._tokens = {token: entry for token, entry in self._tokens.items() if entry[0] > now}
        token = secrets.token_urlsafe(32)
        self._tokens[token] = (now + self._ttl_s, scope_id)
        return token

    def scope_of(self, token: str | None) -> str | None:
        """The scope a viewer token is for, or None."""
        entry = self._tokens.get(token) if token else None
        if entry is None or entry[0] <= self._clock():
            return None
        return entry[1]
