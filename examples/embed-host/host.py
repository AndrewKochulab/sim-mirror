#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""A web page's own backend, for the iframe and web-component examples: it serves the page and mints embed tickets.

A page that shows a SimMirror viewer never holds a SimMirror token. Its backend does: it asks the daemon for a one-shot
embed ticket (``POST /api/v1/scopes/<scope>/embed-tickets``) with a viewer token for that scope, and gives the page only
the ticket, which opens the viewer once, within a minute.

    sim-mirror token create --kind viewer --scope demo --label "example page"
    SIM_MIRROR_TOKEN=<that token> python3 examples/embed-host/host.py --page examples/iframe

Standard library only; it listens on 127.0.0.1.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote

DEFAULT_SERVER = "http://127.0.0.1:7466"
DEFAULT_PORT = 7483
DEFAULT_SCOPE = "demo"
TOKEN_ENV = "SIM_MIRROR_TOKEN"
TICKET_PATH = "/api/embed-ticket"
LIBRARY_PATH = "/sim-mirror.js"
#: The viewer library, as `npm run build` in viewer/ leaves it.
LIBRARY = Path(__file__).resolve().parents[2] / "viewer" / "dist" / "index.js"
NOT_BUILT = "the viewer library is not built: run `npm ci && npm run build` in viewer/"
TIMEOUT_S = 5.0

Opener = Callable[[urllib.request.Request, float], Any]


class TicketError(Exception):
    """A ticket SimMirror did not give, said so a person can act on it."""


def direct_opener() -> Opener:
    """An opener that ignores proxy settings: the daemon is on this machine."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return lambda request, timeout: opener.open(request, timeout=timeout)


def _refusal(exc: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(exc.read().decode("utf-8"))
    except (ValueError, UnicodeDecodeError, OSError):
        body = None
    said = body.get("detail") if isinstance(body, dict) else None
    return said if isinstance(said, str) and said else "no reason given"


def mint_ticket(server: str, scope: str, token: str, opener: Opener) -> dict[str, str]:
    """A one-shot embed ticket for `scope`, and where it opens the viewer: all a page needs, and nothing that lasts."""
    server = server.rstrip("/")
    request = urllib.request.Request(
        f"{server}/api/v1/scopes/{quote(scope, safe='')}/embed-tickets",
        data=b"{}",
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with opener(request, TIMEOUT_S) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise TicketError(f"SimMirror refused the ticket (HTTP {exc.code}): {_refusal(exc)}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise TicketError(f"SimMirror could not be reached at {server}: {exc}") from exc
    data = body.get("data") if isinstance(body, dict) else None
    url = data.get("url") if isinstance(data, dict) else None
    if not isinstance(url, str) or "#ticket=" not in url:
        raise TicketError("SimMirror answered without a ticket")
    return {"server": server, "scope": scope, "embed_url": server + url, "ticket": url.split("#ticket=", 1)[1]}


def handler_for(
    page: Path, mint: Callable[[], dict[str, str]], library: Path = LIBRARY
) -> type[SimpleHTTPRequestHandler]:
    """Serves the page's folder, the viewer library at `LIBRARY_PATH`, and a fresh ticket at `TICKET_PATH`."""

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, directory=str(page), **kwargs)

        def do_POST(self) -> None:
            if self.path != TICKET_PATH:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                self._json(HTTPStatus.OK, mint())
            except TicketError as exc:
                self._json(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})

        def do_GET(self) -> None:
            if self.path != LIBRARY_PATH:
                super().do_GET()
            elif not library.is_file():
                self._json(HTTPStatus.NOT_FOUND, {"error": NOT_BUILT})
            else:
                self._send(HTTPStatus.OK, "text/javascript; charset=utf-8", library.read_bytes())

        def _json(self, status: HTTPStatus, body: Mapping[str, str]) -> None:
            self._send(status, "application/json", json.dumps(body).encode("utf-8"))

        def _send(self, status: HTTPStatus, content_type: str, data: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

    return Handler


def parser() -> argparse.ArgumentParser:
    made = argparse.ArgumentParser(description="Serve a page that shows a SimMirror viewer, and mint its tickets.")
    made.add_argument("--page", required=True, help="the folder holding the page's index.html")
    made.add_argument("--scope", default=DEFAULT_SCOPE, help="the scope the page shows")
    made.add_argument("--server", default=DEFAULT_SERVER, help="the SimMirror daemon's address")
    made.add_argument("--port", type=int, default=DEFAULT_PORT, help="the port this page is served on")
    return made


def serve_forever(server: ThreadingHTTPServer) -> None:
    """Serve until Ctrl-C."""
    with suppress(KeyboardInterrupt):
        server.serve_forever()


def main(
    argv: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
    *,
    opener: Opener | None = None,
    run: Callable[[ThreadingHTTPServer], None] = serve_forever,
) -> int:
    args = parser().parse_args(argv)
    token = (os.environ if env is None else env).get(TOKEN_ENV, "")
    if not token:
        print(
            f"{TOKEN_ENV} is not set. Make a viewer token for the page's scope with "
            f"`sim-mirror token create --kind viewer --scope {args.scope}`.",
            file=sys.stderr,
        )
        return 2
    page = Path(args.page)
    if not (page / "index.html").is_file():
        print(f"{page} holds no index.html", file=sys.stderr)
        return 2
    mint = partial(mint_ticket, args.server, args.scope, token, opener or direct_opener())
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(page, mint))
    print(f"http://127.0.0.1:{server.server_port}/ shows {page}, with tickets for the scope {args.scope}")
    try:
        run(server)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
