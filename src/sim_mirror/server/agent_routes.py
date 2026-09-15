# SPDX-License-Identifier: Apache-2.0
"""Where an agent's tools come in: its MCP relay asks for the manifest and hands each call here.

Answered the way the relay reads them -- the manifest's ``tools`` and ``instructions``, and a call's MCP result -- not
in the person routes' envelope. Who is calling is the host's `Authenticator` to answer (a scoped agent token, or a
host's own session credential); its scope is the device it may touch. Whether the scope's agents may use their tools
is read again on every request (`Runtime.refusal`), and a call's body is bounded.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from sim_mirror.core.runtime import Runtime
from sim_mirror.seams import Authenticator, Caller, Refused
from sim_mirror.server.envelope import RuntimeSource, running
from sim_mirror.tools.results import text

BODY_MAX = 256 * 1024


def create_agent_router(runtime: RuntimeSource, auth: Authenticator) -> APIRouter:
    router = APIRouter()

    async def calling(request: Request) -> tuple[Runtime, Caller]:
        try:
            caller = await auth.agent(request)
        except Refused as exc:
            raise HTTPException(exc.status, exc.message) from exc
        return running(runtime), caller

    @router.get("/manifest")
    async def agent_manifest(request: Request) -> dict[str, Any]:
        """The tools this agent may call, and how to use them -- or none, and why."""
        current, caller = await calling(request)
        return await current.manifest(caller.scope)

    @router.post("/call")
    async def agent_call(request: Request) -> dict[str, Any]:
        """Run one tool call for this agent, on its scope's device."""
        current, caller = await calling(request)
        raw = await request.body()
        try:
            body = json.loads(raw) if len(raw) <= BODY_MAX else None
        except (ValueError, UnicodeDecodeError):
            body = None
        if not isinstance(body, dict):
            return text(f"a call is a JSON object of at most {BODY_MAX // 1024} KB", error=True)
        return await current.call(caller, body.get("name"), body.get("arguments"))

    return router
