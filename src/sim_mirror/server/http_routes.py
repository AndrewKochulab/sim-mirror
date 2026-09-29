# SPDX-License-Identifier: Apache-2.0
"""A person's routes for a scope's simulator: its status, starting and stopping it, and its device.

A host mounts the router under its own prefix, which names the scope as a path parameter (`scope_param`):

* ``GET ""`` -- whether the scope can have a simulator, why not, and how its device stands (`ScopeStatus`);
* ``POST ""`` -- bring the device up if it is not, and mint a one-shot ticket for its screen socket, bound to the
  requesting page's origin when there is one (`Started`);
* ``DELETE ""`` -- let the device go; ``?shutdown=true`` shuts it down too;
* ``GET /devices`` -- this Mac's iOS simulators, for a picker; ``PUT /device`` -- use the one a person picked;
* ``POST /device/settings`` -- change how the device looks or where it believes it is, as `sim_device` does
  (`core.device_settings`), put back when the device is let go;
* ``GET /device/touch`` -- whether the scope's real device can be touched, and what a person can do (`TouchSetup`);
  ``POST /device/touch`` -- a person's Set up touch: build WebDriverAgent for it, or start it again (`core.touch`);
* ``POST /recording`` -- start recording the device's screen, as the settings say; ``DELETE /recording`` -- stop and
  keep it (`Recording`); ``GET /recordings`` -- the recordings kept (`RecordingList`); ``GET /recordings/{name}`` --
  one of their files, only ever one the recordings folder holds.

Who is asking is the host's `Authenticator` to answer, before anything else is looked at.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from sim_mirror.core.device_settings import CHANGES, SettingRefused
from sim_mirror.core.manager import SimulatorUnavailable
from sim_mirror.core.recordings import RecordingOptions, RecordingRefused
from sim_mirror.core.runtime import Runtime
from sim_mirror.core.touch import TouchRefused
from sim_mirror.platform.errors import DeviceControlError
from sim_mirror.scope import Scope
from sim_mirror.seams import Authenticator, Refused
from sim_mirror.server.envelope import RuntimeSource, ok, running
from sim_mirror.server.security import origin_of

SCOPE_PARAM = "scope_id"
#: What a kept recording's file is served as.
MEDIA_TYPES = {".mp4": "video/mp4", ".gif": "image/gif"}


class ChosenDevice(BaseModel):
    udid: str = Field(min_length=1, max_length=64)


class SettingAsked(BaseModel):
    """A change by name, with its arguments beside it: ``{"action": "text_size", "size": "large"}``."""

    model_config = ConfigDict(extra="allow")
    action: str = Field(min_length=1, max_length=32)


class RecordingAsked(BaseModel):
    format: Literal["mp4", "gif", "both"] | None = None


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

    @router.post("/device/settings")
    async def device_setting(body: SettingAsked, request: Request) -> dict[str, Any]:
        """Change how the scope's device looks, or where it believes it is."""
        current, scope = await asked(request)
        instance = current.manager.instance(scope)
        if instance is None or instance.session is None:
            raise HTTPException(409, "the device is not running; start it first")
        if body.action not in CHANGES:
            raise HTTPException(400, f"action is one of {', '.join(CHANGES)}")
        needs, change = CHANGES[body.action]
        if needs not in instance.capabilities:
            raise HTTPException(409, f"{instance.name} cannot change its {body.action.replace('_', ' ')} here")
        try:
            said, work = change(body.model_dump(), current.manager.changes(instance))
            await work
        except SettingRefused as exc:
            raise HTTPException(400, str(exc)) from exc
        except DeviceControlError as exc:
            raise HTTPException(502, str(exc)) from exc
        return ok({"said": said})

    @router.get("/device/touch")
    async def touch_status(request: Request) -> dict[str, Any]:
        """Whether the scope's real device can be touched, and what a person can do about it."""
        current, scope = await asked(request)
        return ok(await current.touch.status(scope))

    @router.post("/device/touch")
    async def touch_set_up(request: Request) -> dict[str, Any]:
        """Set touching the scope's real device up, as a person asked."""
        current, scope = await asked(request)
        try:
            return ok(await current.touch.set_up(scope))
        except TouchRefused as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/recordings")
    async def recording_list(request: Request) -> dict[str, Any]:
        """The recordings kept, newest first, and the one under way."""
        current, scope = await asked(request)
        instance = current.manager.instance(scope)
        running = instance.recording.state(current.manager.now()) if instance and instance.recording else None
        return ok({"recordings": current.recordings.listing(current.config.get(scope)), "recording": running})

    @router.get("/recordings/{name}")
    async def recording_file(name: str, request: Request) -> FileResponse:
        """One kept recording's file, to watch or download."""
        current, scope = await asked(request)
        path = current.recordings.file(current.config.get(scope), name)
        if path is None:
            raise HTTPException(404, f"there is no recording {name!r}")
        return FileResponse(path, media_type=MEDIA_TYPES.get(path.suffix, "application/octet-stream"), filename=name)

    @router.post("/recording")
    async def recording_start(request: Request, body: RecordingAsked | None = None) -> dict[str, Any]:
        """Start recording the scope's device, as its settings say."""
        current, scope = await asked(request)
        instance = current.manager.instance(scope)
        if instance is None or instance.session is None:
            raise HTTPException(409, "the device is not running; start it first")
        config = current.config.get(scope)
        try:
            options = RecordingOptions.from_config(config, format=body.format if body else None)
            control = current.manager.control(instance)
            await current.recordings.start(instance, config, control, by="person", options=options)
        except RecordingRefused as exc:
            raise HTTPException(409, str(exc)) from exc
        assert instance.recording is not None
        return ok({"recording": instance.recording.state(current.manager.now())})

    @router.delete("/recording")
    async def recording_stop(request: Request) -> dict[str, Any]:
        """Stop recording the scope's device and keep what was recorded."""
        current, scope = await asked(request)
        instance = current.manager.instance(scope)
        if instance is None or instance.recording is None:
            raise HTTPException(409, "nothing is being recorded")
        try:
            return ok({"recording": await current.recordings.stop(instance)})
        except RecordingRefused as exc:
            raise HTTPException(409, str(exc)) from exc

    return router
