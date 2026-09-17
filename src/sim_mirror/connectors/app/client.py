# SPDX-License-Identifier: Apache-2.0
"""Asking an app for its view hierarchy: one HTTP/1.1 request on 127.0.0.1, answered within a time and a size.

The request is ``GET /v1/hierarchy?max_nodes=<n>`` with the listing's secret in its Authorization header -- never in
the URL, an error or a log -- and ``Connection: close``, so an exchange has no state to keep. The answer is read within
`wire.HEADERS_MAX_BYTES` of headers and `wire.RESPONSE_MAX_BYTES` of body, without chunks, and the whole exchange
within one timeout: an app paused in a debugger or suspended in the background accepts the connection and never
answers.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

from sim_mirror._version import __version__
from sim_mirror.connectors.app import wire
from sim_mirror.connectors.app.discovery import AppListing
from sim_mirror.connectors.app.errors import AppInactive, AppRefused, AppSdkError, AppTimedOut, AppUnreachable

Connect = Callable[..., Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]

INACTIVE = 409


def request(listing: AppListing, max_nodes: int) -> bytes:
    """The request asking `listing`'s app for its hierarchy."""
    return (
        f"GET {wire.HIERARCHY_PATH}?max_nodes={max_nodes} HTTP/1.1\r\n"
        f"Host: {wire.HOST}:{listing.port}\r\n"
        f"Authorization: Bearer {listing.secret}\r\n"
        "Accept: application/json\r\n"
        f"User-Agent: sim-mirror/{__version__}\r\n"
        "Connection: close\r\n\r\n"
    ).encode()


def _head(raw: bytes, listing: AppListing) -> tuple[int, dict[str, str]]:
    """The status and headers of an answer's head."""
    status_line, *lines = raw.decode("latin-1").split("\r\n")
    parts = status_line.split(" ", 2)
    if len(parts) < 2 or not parts[0].startswith("HTTP/1.") or not parts[1].isdigit():
        raise AppSdkError(f"{listing.name} did not answer in HTTP")
    headers: dict[str, str] = {}
    for line in lines:
        name, colon, value = line.partition(":")
        if colon:
            headers[name.strip().lower()] = value.strip()
    return int(parts[1]), headers


async def _body(reader: asyncio.StreamReader, headers: dict[str, str], listing: AppListing) -> bytes:
    too_large = AppSdkError(f"{listing.name} answered with more than {wire.RESPONSE_MAX_BYTES} bytes")
    if "transfer-encoding" in headers:
        raise AppSdkError(f"{listing.name} answered in chunks, which the app SDK protocol does not use")
    length = headers.get("content-length")
    if length is not None:
        if not length.isdigit():
            raise AppSdkError(f"{listing.name} answered with a length that is not a number")
        if int(length) > wire.RESPONSE_MAX_BYTES:
            raise too_large
        return await reader.readexactly(int(length))
    body = bytearray()
    while chunk := await reader.read(65536):
        body.extend(chunk)
        if len(body) > wire.RESPONSE_MAX_BYTES:
            raise too_large
    return bytes(body)


def _refusal(status: int, body: bytes, listing: AppListing) -> AppSdkError:
    code = ""
    with contextlib.suppress(ValueError, RecursionError, AttributeError, TypeError):
        found = json.loads(body)["error"]["code"]
        code = found if isinstance(found, str) and found.isidentifier() else ""
    if status == INACTIVE:
        return AppInactive(f"{listing.name} is not in front")
    reason = f"{status} {code}".strip()
    return AppRefused(f"{listing.name} refused to share its view hierarchy ({reason})", status)


async def _exchange(listing: AppListing, max_nodes: int, connect: Connect) -> dict[str, Any]:
    try:
        reader, writer = await connect(wire.HOST, listing.port, limit=wire.HEADERS_MAX_BYTES)
    except OSError as exc:
        raise AppUnreachable(f"{listing.name} is not listening any more") from exc
    try:
        writer.write(request(listing, max_nodes))
        await writer.drain()
        head = await reader.readuntil(b"\r\n\r\n")
        status, headers = _head(head[:-4], listing)
        body = await _body(reader, headers, listing)
    except asyncio.LimitOverrunError as exc:
        raise AppSdkError(f"{listing.name} answered with headers longer than {wire.HEADERS_MAX_BYTES} bytes") from exc
    except (asyncio.IncompleteReadError, OSError) as exc:
        raise AppUnreachable(f"{listing.name} hung up before answering") from exc
    finally:
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()
    if status != 200:
        raise _refusal(status, body, listing)
    try:
        answer = json.loads(body)
    except (ValueError, RecursionError) as exc:
        raise AppSdkError(f"{listing.name} answered with something other than JSON") from exc
    if not isinstance(answer, dict):
        raise AppSdkError(f"{listing.name} answered with something other than a view hierarchy")
    return answer


async def fetch_hierarchy(
    listing: AppListing,
    *,
    max_nodes: int,
    timeout_s: float,
    connect: Connect = asyncio.open_connection,
) -> dict[str, Any]:
    """What `listing`'s app answers for its hierarchy, as it sent it. Raises an `AppSdkError` saying why not."""
    try:
        return await asyncio.wait_for(_exchange(listing, max_nodes, connect), timeout_s)
    except asyncio.TimeoutError as exc:
        raise AppTimedOut(f"{listing.name} did not answer within {round(timeout_s * 1000)} ms") from exc
