# SPDX-License-Identifier: Apache-2.0
"""A person's routes for a scope's simulator: its status, starting and stopping it, and its device.

A host mounts the router under its own prefix, which names the scope as a path parameter (`scope_param`):

* ``GET ""`` -- whether the scope can have a simulator, why not, and how its device stands (`ScopeStatus`);
* ``POST ""`` -- bring the device up if it is not, and mint a one-shot ticket for its screen socket, bound to the
  requesting page's origin when there is one (`Started`);
* ``DELETE ""`` -- let the device go; ``?shutdown=true`` shuts it down too;
* ``GET /devices`` -- this Mac's iOS simulators, for a picker; ``PUT /device`` -- use the one a person picked.

Who is asking is the host's `Authenticator` to answer, before anything else is looked at.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from sim_mirror.core.manager import SimulatorUnavailable
from sim_mirror.core.runtime import Runtime
from sim_mirror.scope import Scope
from sim_mirror.seams import Authenticator, Refused
from sim_mirror.server.envelope import RuntimeSource, ok, running
from sim_mirror.server.security import origin_of

SCOPE_PARAM = "scope_id"


class ChosenDevice(BaseModel):
    udid: str = Field(min_length=1, max_length=64)


def create_http_router(runtime: RuntimeSource, auth: Authenticator, *, scope_param: str = SCOPE_PARAM) -> APIRouter:
    router = APIRouter()

    async def person(request: Request) -> Scope:
        try:
            return (await auth.person(request, str(request.path_params.get(scope_param, "")))).scope
        except Refused as exc:
            raise HTTPException(exc.status, exc.message) from exc

    async def asked(request: Request) -> tuple[Runtime, Scope]:
        scope = await person(request)
        return running(runtime), scope

    @router.get("")
    async def simulator_status(request: Request) -> dict[str, Any]:
        """Whether the scope can have a simulator, why not, and how its device stands."""
        current, scope = await asked(request)
        return ok(await current.manager.status(scope))

    @router.post("")
    async def simulator_start(request: Request) -> dict[str, Any]:
        """Bring the scope's simulator up if it is not, and mint a ticket for its screen."""
        current, scope = await asked(request)
        try:
            instance = await current.manager.ensure(scope)
        except SimulatorUnavailable as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        ticket = current.manager.mint_ticket(instance, origin=origin_of(request.headers.get("origin")))
        return ok({**(await current.manager.status(scope)), "ticket": ticket})

    @router.delete("")
    async def simulator_stop(request: Request, shutdown: bool = Query(False)) -> dict[str, Any]:
        """Let the scope's simulator go; with ``shutdown``, shut the device down too."""
        scope = await person(request)
        current = runtime()
        stopped = current is not None and await current.manager.stop(scope, shutdown_device=shutdown)
        return ok({"stopped": stopped})

    @router.get("/devices")
    async def simulator_devices(request: Request) -> dict[str, Any]:
        """This Mac's iOS simulators, for a picker."""
        current, scope = await asked(request)
        try:
            return ok({"devices": await current.manager.devices(scope)})
        except SimulatorUnavailable as exc:
            raise HTTPException(exc.status, str(exc)) from exc

    @router.put("/device")
    async def simulator_choose(body: ChosenDevice, request: Request) -> dict[str, Any]:
        """Use this simulator for the scope from now on."""
        current, scope = await asked(request)
        try:
            await current.manager.choose(scope, body.udid)
        except SimulatorUnavailable as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        return ok({"udid": body.udid})

    return router
