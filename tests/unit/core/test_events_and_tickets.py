# SPDX-License-Identifier: Apache-2.0
"""A device's events reach every subscriber, a slow one losing its oldest; its tickets open a screen once, from the
origin they were minted for, for a short while."""

from __future__ import annotations

from sim_mirror.core.events import EventBus
from sim_mirror.core.tickets import TicketBook
from sim_mirror.protocol import TICKET_TTL_S


def test_every_subscriber_gets_every_event_until_it_leaves() -> None:
    bus = EventBus()
    one, two = bus.subscribe(), bus.subscribe()
    assert bus.listeners == 2
    bus.publish({"type": "status", "state": "ready"})
    assert one.get_nowait() == two.get_nowait() == {"type": "status", "state": "ready"}
    bus.unsubscribe(two)
    bus.unsubscribe(two)
    bus.publish({"type": "agent"})
    assert one.get_nowait() == {"type": "agent"} and two.empty() and bus.listeners == 1


def test_a_subscriber_that_stops_reading_loses_its_oldest_events() -> None:
    bus = EventBus(maxsize=2)
    queue = bus.subscribe()
    for index in range(4):
        bus.publish({"n": index})
    assert [queue.get_nowait()["n"] for _ in range(queue.qsize())] == [2, 3]


def test_a_ticket_opens_the_screen_once() -> None:
    book = TicketBook()
    ticket = book.mint(100.0)
    assert ticket in book and len(book) == 1
    assert book.consume(ticket, 100.0) is True
    assert book.consume(ticket, 100.0) is False
    assert ticket not in book and len(book) == 0


def test_a_ticket_expires_after_its_life() -> None:
    book = TicketBook()
    ticket = book.mint(0.0)
    assert book.consume(ticket, TICKET_TTL_S) is False and ticket not in book


def test_an_empty_or_unknown_ticket_opens_nothing() -> None:
    book = TicketBook()
    book.mint(0.0)
    assert book.consume("", 0.0) is False and book.consume("forged", 0.0) is False
    assert len(book) == 1


def test_minting_drops_what_expired() -> None:
    book = TicketBook(ttl=10.0)
    old = book.mint(0.0)
    fresh = book.mint(11.0)
    assert old not in book and fresh in book
    assert len({book.mint(11.0) for _ in range(5)}) == 5


def test_a_ticket_minted_for_an_origin_opens_only_from_it_and_is_spent_by_any_try() -> None:
    book = TicketBook()
    bound = book.mint(0.0, origin="http://localhost:3000")
    assert book.consume(bound, 0.0, origin="http://evil.test") is False
    assert book.consume(bound, 0.0, origin="http://localhost:3000") is False
    again = book.mint(0.0, origin="http://localhost:3000")
    assert book.consume(again, 0.0, origin="http://localhost:3000") is True
    anywhere = book.mint(0.0)
    assert book.consume(anywhere, 0.0, origin="http://any.test") is True
