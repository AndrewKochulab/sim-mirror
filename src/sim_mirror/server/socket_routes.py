# SPDX-License-Identifier: Apache-2.0
"""A scope's screen socket, ``WS /screen?ticket=…``: frames and events out, a whitelist of a person's input in.

**Never a connector's own protocol.** A socket is let in in this order:

1. **the host's `Authenticator.admit_socket`** -- Host, Origin and whatever else the host checks, refused before the
   upgrade where it can be;
2. **the upgrade** -- from here every refusal is a close code and a reason the viewer can read;
3. **whether the scope can still have a simulator** -- checked here as well as when the ticket was minted, because a
   simulator switched off since must not keep a socket open (4403);
4. **the ticket**, one-shot, for this scope and, when it was minted for a page, that page's origin (4401).

Then the relay's hello (`core.screen_relay`).
"""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketState

from sim_mirror.core.screen_relay import ScreenSocket
from sim_mirror.protocol import CLOSE_BAD_GATEWAY, CLOSE_FORBIDDEN, CLOSE_UNAUTHORIZED
from sim_mirror.seams import Authenticator, Refused
from sim_mirror.server.envelope import NOT_RUNNING, RuntimeSource
from sim_mirror.server.http_routes import SCOPE_PARAM
from sim_mirror.server.security import origin_of

BAD_TICKET = "invalid or expired ticket"


def create_socket_router(runtime: RuntimeSource, auth: Authenticator, *, scope_param: str = SCOPE_PARAM) -> APIRouter:
    router = APIRouter()

    @router.websocket("/screen")
    async def simulator_screen(websocket: WebSocket) -> None:
        try:
            admission = await auth.admit_socket(websocket, str(websocket.path_params.get(scope_param, "")))
        except Refused as exc:
            await websocket.close(code=exc.status, reason=exc.message)
            return
        if websocket.client_state == WebSocketState.CONNECTING:
            await websocket.accept()
        current = runtime()
        if current is None:
            await websocket.close(code=CLOSE_BAD_GATEWAY, reason=NOT_RUNNING)
            return
        reason = await current.manager.unavailable(admission.scope)
        if reason:
            await websocket.close(code=CLOSE_FORBIDDEN, reason=reason)
            return
        ticket = websocket.query_params.get("ticket", "")
        origin = origin_of(websocket.headers.get("origin"))
        instance = current.manager.consume_ticket(admission.scope, ticket, origin=origin)
        if instance is None:
            await websocket.close(code=CLOSE_UNAUTHORIZED, reason=BAD_TICKET)
            return
        await current.relay(cast(ScreenSocket, websocket), instance).run()

    return router
