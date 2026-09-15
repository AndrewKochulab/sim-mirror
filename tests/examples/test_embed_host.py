# SPDX-License-Identifier: Apache-2.0
"""The embed host: it serves a page, the viewer library and one-shot tickets, and never hands the page its token."""

from __future__ import annotations

import io
import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from email.message import Message
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import host
import pytest

DAEMON = "http://127.0.0.1:7466"


class Daemon:
    """The daemon's embed-tickets route: records each request, answers what a test sets."""

    def __init__(self, answer: Any = None, error: Exception | None = None) -> None:
        self.answer = (
            {"ok": True, "data": {"url": "/embed/demo#ticket=t1ck3t", "expires_in_s": 60}} if answer is None else answer
        )
        self.error = error
        self.requests: list[urllib.request.Request] = []

    def __call__(self, request: urllib.request.Request, timeout: float) -> io.BytesIO:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return io.BytesIO(json.dumps(self.answer).encode("utf-8"))


def refused(status: int, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(f"{DAEMON}/x", status, "refused", Message(), io.BytesIO(body))


def page(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "index.html").write_text("<p>my page</p>")
    return folder


@contextmanager
def serving(folder: Path, daemon: Daemon, library: Path) -> Iterator[str]:
    mint = lambda: host.mint_ticket(DAEMON, "demo", "viewer-token", daemon)  # noqa: E731
    server = ThreadingHTTPServer(("127.0.0.1", 0), host.handler_for(folder, mint, library))
    thread = threading.Thread(target=host.serve_forever, args=(server,), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def fetch(url: str, method: str = "GET") -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(url, method=method, data=b"" if method == "POST" else None)
    try:
        with host.direct_opener()(request, 5.0) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        with exc:
            return exc.code, dict(exc.headers), exc.read()


def test_a_ticket_is_minted_with_the_token_and_only_the_ticket_reaches_the_page(tmp_path: Path) -> None:
    daemon = Daemon()
    library = tmp_path / "index.js"
    library.write_text("export {}")
    with serving(page(tmp_path / "page"), daemon, library) as base:
        status, headers, body = fetch(base + host.TICKET_PATH, "POST")
        index = fetch(base + "/")
        script = fetch(base + host.LIBRARY_PATH)
        wrong = fetch(base + "/api/other", "POST")
    assert status == 200 and headers["Cache-Control"] == "no-store"
    assert json.loads(body) == {
        "server": DAEMON,
        "scope": "demo",
        "embed_url": f"{DAEMON}/embed/demo#ticket=t1ck3t",
        "ticket": "t1ck3t",
    }
    assert b"viewer-token" not in body
    [request] = daemon.requests
    assert request.full_url == f"{DAEMON}/api/v1/scopes/demo/embed-tickets" and request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer viewer-token"
    assert index[0] == 200 and index[2] == b"<p>my page</p>"
    assert script[0] == 200 and script[1]["Content-Type"].startswith("text/javascript") and script[2] == b"export {}"
    assert wrong[0] == 404


def test_the_page_is_told_why_there_is_no_ticket_or_library(tmp_path: Path) -> None:
    daemon = Daemon(error=refused(401, b'{"detail": "authentication required"}'))
    with serving(page(tmp_path / "page"), daemon, tmp_path / "not-built.js") as base:
        status, _, body = fetch(base + host.TICKET_PATH, "POST")
        missing = fetch(base + host.LIBRARY_PATH)
    assert status == 502
    assert json.loads(body) == {"error": "SimMirror refused the ticket (HTTP 401): authentication required"}
    assert missing[0] == 404 and json.loads(missing[2]) == {"error": host.NOT_BUILT}


@pytest.mark.parametrize(
    ("daemon", "said"),
    [
        (Daemon(error=refused(403, b"not json")), "(HTTP 403): no reason given"),
        (Daemon(error=urllib.error.URLError("connection refused")), "could not be reached at http://127.0.0.1:7466"),
        (Daemon(answer={"ok": True, "data": {"url": "/embed/demo"}}), "answered without a ticket"),
        (Daemon(answer={"ok": True, "data": "nope"}), "answered without a ticket"),
        (Daemon(answer=["not", "an", "object"]), "answered without a ticket"),
    ],
)
def test_every_way_a_ticket_is_not_given_is_said(daemon: Daemon, said: str) -> None:
    with pytest.raises(host.TicketError, match=said.replace("(", r"\(").replace(")", r"\)")):
        host.mint_ticket(DAEMON + "/", "demo", "viewer-token", daemon)


def test_a_scope_is_quoted_into_the_path() -> None:
    daemon = Daemon()
    host.mint_ticket(DAEMON, "ws:a/b", "viewer-token", daemon)
    assert daemon.requests[0].full_url.endswith("/api/v1/scopes/ws%3Aa%2Fb/embed-tickets")


def test_main_needs_a_token_and_a_page(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert host.main(["--page", str(page(tmp_path / "page"))], env={}) == 2
    assert "sim-mirror token create --kind viewer --scope demo" in capsys.readouterr().err
    assert host.main(["--page", str(tmp_path / "empty")], env={host.TOKEN_ENV: "t"}) == 2
    assert "holds no index.html" in capsys.readouterr().err


def test_main_serves_until_it_is_stopped(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    served: list[ThreadingHTTPServer] = []
    folder = page(tmp_path / "page")
    argv = ["--page", str(folder), "--port", "0", "--scope", "demo"]
    assert host.main(argv, env={host.TOKEN_ENV: "t"}, opener=Daemon(), run=served.append) == 0
    assert len(served) == 1
    assert f"shows {folder}, with tickets for the scope demo" in capsys.readouterr().out


def test_an_interrupt_ends_serving_quietly() -> None:
    class Interrupted:
        def serve_forever(self) -> None:
            raise KeyboardInterrupt

    interrupted: Any = Interrupted()
    host.serve_forever(interrupted)
