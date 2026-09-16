# SPDX-License-Identifier: Apache-2.0
"""Sensitive settings changes a page asked for, held until a person confirms them at the terminal.

A setting that decides what SimMirror runs or who may reach it is not changed on a page's word alone: a page is
untrusted, and one that could turn on command execution or add an origin could let anything in. So such a change waits
here, known by a digest of exactly what it changes. ``sim-mirror settings confirm`` -- with the admin token, which no
page ever holds -- shows each waiting change and its code; a person who recognises the change types the code into the
page, and the page sends the change again with it.

A code is a short run of letters and digits that cannot be misread, bound to its change: it confirms that change and no
other, is spent once, lasts `CONFIRM_TTL_S`, and the change is dropped after `ATTEMPTS` wrong codes, so guessing
starts again from a code nobody has shown.
"""

from __future__ import annotations

import hmac
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass

from sim_mirror.protocol import PendingConfirmation
from sim_mirror.scope import Scope

CONFIRM_TTL_S = 300.0
ATTEMPTS = 5
#: At most this many changes wait at once; asking for another drops the oldest.
WAITING_MAX = 8
CODE_LENGTH = 8
#: No 0/O, 1/I/L, or 5/S: a code is read off a terminal and typed by hand.
ALPHABET = "ABCDEFGHJKMNPQRTUVWXYZ2346789"
CONFIRM_COMMAND = "sim-mirror settings confirm"


@dataclass
class Waiting:
    id: str
    scope_id: str
    digest: str
    summary: str
    code: str
    expires_at: float
    wrong: int = 0

    def shown(self) -> str:
        """The code as a person reads it: two groups of four."""
        return f"{self.code[:4]}-{self.code[4:]}"

    def expires_in_s(self, now: float) -> float:
        return max(0.0, round(self.expires_at - now, 1))


def normalized(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch.isalnum())


class PendingChanges:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic, ttl_s: float = CONFIRM_TTL_S) -> None:
        self._clock = clock
        self._ttl_s = ttl_s
        self._waiting: dict[str, Waiting] = {}

    def _prune(self) -> float:
        now = self._clock()
        self._waiting = {digest: held for digest, held in self._waiting.items() if held.expires_at > now}
        return now

    def request(self, scope: Scope, digest: str, summary: str) -> PendingConfirmation:
        now = self._prune()
        held = self._waiting.get(digest)
        if held is None:
            while len(self._waiting) >= WAITING_MAX:
                self._waiting.pop(min(self._waiting, key=lambda key: self._waiting[key].expires_at))
            code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
            held = Waiting(secrets.token_urlsafe(9), scope.id, digest, summary, code, now + self._ttl_s)
            self._waiting[digest] = held
        return {
            "id": held.id,
            "summary": held.summary,
            "command": CONFIRM_COMMAND,
            "expires_in_s": held.expires_in_s(now),
        }

    def confirm(self, digest: str, code: str) -> bool:
        self._prune()
        held = self._waiting.get(digest)
        if held is None:
            return False
        if hmac.compare_digest(normalized(code), held.code):
            del self._waiting[digest]
            return True
        held.wrong += 1
        if held.wrong >= ATTEMPTS:
            del self._waiting[digest]
        return False

    def waiting(self) -> list[dict[str, object]]:
        """Every change waiting, oldest first, with its code: for the admin token only."""
        now = self._prune()
        return [
            {
                "id": held.id,
                "scope": held.scope_id,
                "summary": held.summary,
                "code": held.shown(),
                "expires_in_s": held.expires_in_s(now),
            }
            for held in sorted(self._waiting.values(), key=lambda held: held.expires_at)
        ]
