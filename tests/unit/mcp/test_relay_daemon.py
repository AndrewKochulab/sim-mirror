# SPDX-License-Identifier: Apache-2.0
"""The relay `sim-mirror mcp` runs, against the real daemon app with nothing faked between them: the headers the
launcher gives the relay are the ones the daemon reads."""

from __future__ import annotations

import asyncio
import io
import json
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path

import httpx

from sim_mirror.daemon import auth
from sim_mirror.daemon.app import build_daemon, create_app
from sim_mirror.daemon.tokens import TokenStore
from sim_mirror.mcp import launcher, relay
from sim_mirror.testing.asgi import HOST
from sim_mirror.testing.fakes import no_wait
from sim_mirror.testing.rig import DeviceRig


class Answer:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def read(self) -> bytes:
        return self.body

    def __enter__(self) -> Answer:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def test_the_launchers_headers_are_the_ones_the_daemon_reads() -> None:
    assert (launcher.TOKEN_HEADER.lower(), launcher.SCOPE_HEADER.lower(), launcher.CLIENT_HEADER.lower()) == (
        auth.TOKEN_HEADER,
        auth.SCOPE_HEADER,
        auth.CLIENT_HEADER,
    )


async def test_an_agent_reaches_its_tools_through_the_relay_and_the_real_daemon(tmp_path: Path) -> None:
    rig = DeviceRig(tmp_path)
    tokens = TokenStore(tmp_path / "secrets")
    daemon = build_daemon(
        config=rig.config,
        state=rig.state,
        memory=rig.memory,
        tokens=tokens,
        port=7466,
        copy=rig.copy,
        registry=rig.registry,
        claims=rig.claims,
        xcrun=rig.xcrun,
        static_dir=None,
        clock=rig.clock,
        sleep=no_wait,
    )
    app = create_app(daemon)
    _record, token = tokens.create("agent", ["tp-1"], label="relay")
    loop = asyncio.get_running_loop()

    async def ask(method: str, path: str, headers: dict[str, str], body: bytes | None) -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=f"http://{HOST}") as http:
            return await http.request(method, path, headers=headers, content=body)

    def opener(request: urllib.request.Request, timeout: float) -> Answer:
        path = request.full_url.removeprefix(f"http://{HOST}")
        body = request.data if isinstance(request.data, bytes) else None
        call = ask(request.get_method(), path, dict(request.header_items()), body)
        answered = asyncio.run_coroutine_threadsafe(call, loop).result(timeout=10)
        if answered.status_code >= 400:
            raise urllib.error.HTTPError(
                request.full_url, answered.status_code, "refused", Message(), io.BytesIO(answered.content)
            )
        return Answer(answered.content)

    env = {
        launcher.URL_ENV: f"http://{HOST}{launcher.AGENT_PATH}",
        launcher.TOKEN_ENV: token,
        launcher.SCOPE_ENV: "tp-1",
    }
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"clientInfo": {"name": "codex"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "sim_device", "arguments": {}}},
    ]
    stdin = io.StringIO("".join(json.dumps(message) + "\n" for message in messages))
    stdout = io.StringIO()
    answered = await asyncio.to_thread(
        relay.main, launcher.relay_argv(), env=env, stdin=stdin, stdout=stdout, opener=opener
    )
    assert answered == 0
    replies = {reply["id"]: reply for reply in map(json.loads, stdout.getvalue().splitlines()) if "id" in reply}
    assert "refused" not in replies[1]["result"]["instructions"]
    assert "sim_snapshot" in [tool["name"] for tool in replies[2]["result"]["tools"]]
    assert auth.AUTHENTICATION_REQUIRED not in json.dumps(replies[3])
