# SPDX-License-Identifier: Apache-2.0
"""One-shot tickets for a device's screen socket.

A browser opening a WebSocket cannot send a header, and a token in a URL ends up in logs. So a screen socket is opened
with a ticket that only the authenticated request starting the device mints: random, good for one connection, gone
after `TICKET_TTL_S` whether it was used or not -- and, when it was minted for a page on one origin, good only for a
socket from that origin.
"""

from __future__ import annotations

import secrets

from sim_mirror.protocol import TICKET_TTL_S


class TicketBook:
    """The tickets one device has minted and not yet seen used."""

    def __init__(self, ttl: float = TICKET_TTL_S) -> None:
        self._ttl = ttl
        self._tickets: dict[str, tuple[float, str | None]] = {}

    def mint(self, now: float, *, origin: str | None = None) -> str:
        """A new ticket, for any origin or only `origin`. Expired ones are dropped here, so the book stays small."""
        self._tickets = {ticket: entry for ticket, entry in self._tickets.items() if entry[0] > now}
        ticket = secrets.token_urlsafe(32)
        self._tickets[ticket] = (now + self._ttl, origin)
        return ticket

    def consume(self, ticket: str, now: float, *, origin: str | None = None) -> bool:
        """Whether this ticket opens the screen now, from `origin`. Either way it opens nothing again."""
        entry = self._tickets.pop(ticket, None) if ticket else None
        if entry is None:
            return False
        expiry, bound = entry
        return expiry > now and (bound is None or bound == origin)

    def __contains__(self, ticket: object) -> bool:
        return ticket in self._tickets

    def __len__(self) -> int:
        return len(self._tickets)
