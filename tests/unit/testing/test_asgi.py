# SPDX-License-Identifier: Apache-2.0
"""The in-loop socket driver a host's tests use: a socket the app accepts, talks on and closes, or refuses."""

from __future__ import annotations

import asyncio

from starlette.applications import Starlette
from starlette.routing import WebSocketRoute
from starlette.websockets import WebSocket

from sim_mirror.testing.asgi import AsgiSocket


async def echo(websocket: WebSocket) -> None:
    await websocket.accept()
    await websocket.send_text(await websocket.receive_text())
    await websocket.close(code=4410, reason="done")


async def refuse(websocket: WebSocket) -> None:
    await websocket.close(code=4403, reason="no")


APP = Starlette(routes=[WebSocketRoute("/echo", echo), WebSocketRoute("/refuse", refuse)])


async def test_a_socket_the_app_accepts_answers_and_then_closes_on_its_own() -> None:
    socket = AsgiSocket(APP, "/echo")
    async with socket as ws:
        assert await ws.accepted()
        await ws.say({"hello": 1})
        assert await ws.text() == {"hello": 1}
        assert await ws.text() is None and ws.closed == (4410, "done")
        await ws.ended()
        await ws.ended()
    await socket.leave()


async def test_a_socket_the_app_refuses_is_let_go_without_a_disconnect() -> None:
    async with AsgiSocket(APP, "/refuse") as ws:
        assert await ws.accepted() is False and ws.closed == (4403, "no")
        await asyncio.sleep(0)
