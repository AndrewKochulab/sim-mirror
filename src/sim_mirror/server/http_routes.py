# SPDX-License-Identifier: Apache-2.0
"""A person's routes for a scope's simulator: its status, starting and stopping it, and its device.

A host mounts the router under its own prefix, which names the scope as a path parameter (`scope_param`):

* ``GET ""`` -- whether the scope can have a simulator, why not, and how its device stands (`ScopeStatus`);
* ``POST ""`` -- bring the device up if it is not, and mint a one-shot ticket for its screen socket, bound to the
  requesting page's origin when there is one (`Started`);
* ``DELETE ""`` -- let the device go; ``?shutdown=true`` shuts a simulator down too, never a real device (`Stopped`);
* ``GET /devices`` -- the devices the scope could use, for a picker, each with the scope's agents working on it;
  ``PUT /device`` -- use the one a person picked as the scope's own device: the screens showing the old one come back
  by themselves on it, and the agents working on the old one move with it;
* ``POST /device/settings`` -- change how the device looks or where it believes it is, as `sim_device` does
  (`core.device_settings`), put back when the device is let go;
* ``GET /device/touch`` -- whether the scope's real device can be touched, and what a person can do (`TouchSetup`);
  ``POST /device/touch`` -- a person's Set up touch: build WebDriverAgent for it, or start it again (`core.touch`);
* ``POST /recording`` -- start recording the device's screen, as the settings say, or with the ``format``, ``width``,
  ``quality``, ``preset``, ``also``, ``max_seconds`` or ``label`` the body asks for; ``DELETE /recording`` -- stop and
  keep it, or answer the one that stopped by itself, once kept or after a while still rendering (`RecordingStopped`);
  ``GET /recordings`` -- the recordings kept, and those still being rendered (`RecordingList`);
  ``GET /recordings/{name}`` -- one of their files, only ever one the recordings folder holds.

Each route but ``/devices``, ``PUT /device`` and a recording's file acts on the scope's own device -- the one
``PUT /device`` picks -- or, given ``?udid=``, on that one of its devices instead: one it runs, or one it could use,
brought up beside its own by ``POST ""`` (`DeviceManager.ensure`). That is how two viewer tabs of one scope show a
device each, without moving its agents or each other. A UDID that is none of the scope's is not found (404).

Who is asking is the host's `Authenticator` to answer, before anything else is looked at.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from sim_mirror.connectors.base import ConnectorError
from sim_mirror.core.actions import ActionError
from sim_mirror.core.device_settings import CHANGES, SettingRefused
from sim_mirror.core.manager import SimulatorUnavailable
from sim_mirror.core.recordings import LABEL_LENGTH, MAX_SECONDS, WIDTHS, RecordingOptions, RecordingRefused
from sim_mirror.core.runtime import Runtime
from sim_mirror.core.touch import TouchRefused
from sim_mirror.platform.errors import DeviceControlError
from sim_mirror.platform.identifiers import kind_of
from sim_mirror.protocol import Agent
from sim_mirror.scope import Scope
from sim_mirror.seams import Authenticator, Refused
from sim_mirror.server.envelope import RuntimeSource, ok, running
from sim_mirror.server.security import origin_of

SCOPE_PARAM = "scope_id"
#: What a kept recording's file is served as.
MEDIA_TYPES = {".mp4": "video/mp4", ".gif": "image/gif"}
#: The longest UDID a route is asked about: as long as a device a person picks may be named.
UDID_MAX = 64


class ChosenDevice(BaseModel):
    udid: str = Field(min_length=1, max_length=UDID_MAX)


class SettingAsked(BaseModel):
    """A change by name, with its arguments beside it: ``{"action": "text_size", "size": "large"}``."""

    model_config = ConfigDict(extra="allow")
    action: str = Field(min_length=1, max_length=32)


class RecordingAsked(BaseModel):
    format: Literal["mp4", "gif", "both"] | None = None
    width: int | None = Field(default=None, ge=WIDTHS[0], le=WIDTHS[1])
    quality: Literal["low", "medium", "high"] | None = None
    preset: Literal["pr"] | None = None
    also: Literal["pr"] | None = None
    max_seconds: int | None = Field(default=None, ge=MAX_SECONDS[0], le=MAX_SECONDS[1])
    label: str | None = Field(default=None, min_length=1, max_length=LABEL_LENGTH)
    fps: Literal[30, 60, 120] | None = None


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

    async def named(current: Runtime, scope: Scope, udid: str | None) -> str | None:
        """The device a request names with ``?udid=``, when it names one of the scope's: one it runs, its own, or one
        it could use. Not found when it is none of those."""
        if udid is None or udid == current.manager.default_udid(scope) or current.manager.instance(scope, udid):
            return udid
        try:
            listed = await current.manager.devices(scope)
        except SimulatorUnavailable as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        if all(choice["udid"] != udid for choice in listed):
            raise HTTPException(404, current.copy.no_such_device(kind_of(udid) or "simulator"))
        return udid

    async def on_device(request: Request, udid: str | None) -> tuple[Runtime, Scope, str | None]:
        """Who is asking, and about which of the scope's devices: the one named by ``?udid=``, else its own (None)."""
        current, scope = await asked(request)
        return current, scope, await named(current, scope, udid)

    def which(udid: str | None = Query(None, min_length=1, max_length=UDID_MAX)) -> str | None:
        return udid

    def working(current: Runtime, scope: Scope, udid: str) -> list[Agent]:
        """The scope's live agents working on a device, the first to come first."""
        on = current.selections.on(udid)
        return [{"key": agent.key, "title": agent.title} for agent in on if agent.scope.id == scope.id]

    @router.get("")
    async def simulator_status(request: Request, udid: str | None = Depends(which)) -> dict[str, Any]:
        """Whether the scope can have a simulator, why not, and how its device -- or the one named -- stands."""
        current, scope, udid = await on_device(request, udid)
        return ok(await current.manager.status(scope, udid))

    @router.post("")
    async def simulator_start(request: Request, udid: str | None = Depends(which)) -> dict[str, Any]:
        """Bring the scope's simulator -- or the one of its devices named -- up if it is not, and mint a ticket for its
        screen."""
        current, scope, udid = await on_device(request, udid)
        try:
            instance = await current.manager.ensure(scope, udid)
        except SimulatorUnavailable as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        ticket = current.manager.mint_ticket(instance, origin=origin_of(request.headers.get("origin")))
        return ok({**(await current.manager.status(scope, udid)), "ticket": ticket})

    @router.delete("")
    async def simulator_stop(
        request: Request, shutdown: bool = Query(False), udid: str | None = Depends(which)
    ) -> dict[str, Any]:
        """Let the scope's simulator -- or the one of its devices named -- go; with ``shutdown``, shut the device down
        too -- one running on this Mac: a real device is never shut down."""
        scope = await person(request)
        current = runtime()
        if current is not None:
            udid = await named(current, scope, udid)
        instance = current.manager.instance(scope, udid) if current is not None else None
        here = current is not None and instance is not None and current.manager.runs_here(instance)
        stopped = current is not None and await current.manager.stop(scope, shutdown_device=shutdown, udid=udid)
        kind = instance.kind if instance is not None else None
        return ok({"stopped": stopped, "shut_down": stopped and shutdown and here, "kind": kind})

    @router.get("/capture")
    async def capture_frame(request: Request, udid: str | None = Depends(which)) -> dict[str, Any]:
        current, scope, udid = await on_device(request, udid)
        instance = current.manager.instance(scope, udid)
        if instance is None:
            raise HTTPException(409, "Open the device before capturing its screen.")
        try:
            return ok(await current.actions.capture_scene(instance))
        except (ActionError, ConnectorError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/devices")
    async def simulator_devices(request: Request) -> dict[str, Any]:
        """The devices the scope could use, for a picker, with who works on each."""
        current, scope = await asked(request)
        try:
            listed = await current.manager.devices(scope)
        except SimulatorUnavailable as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        return ok({"devices": [{**choice, "agents": working(current, scope, choice["udid"])} for choice in listed]})

    @router.put("/device")
    async def simulator_choose(body: ChosenDevice, request: Request) -> dict[str, Any]:
        """Use this device as the scope's own from now on. The screens showing the old one are told it restarts, and
        come back by themselves -- on this one, unless they name a device of their own."""
        current, scope = await asked(request)
        try:
            await current.manager.choose(scope, body.udid, restarting=True)
        except SimulatorUnavailable as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        return ok({"udid": body.udid})

    @router.post("/device/settings")
    async def device_setting(body: SettingAsked, request: Request, udid: str | None = Depends(which)) -> dict[str, Any]:
        """Change how the scope's device -- or the one of its devices named -- looks, or where it believes it is."""
        current, scope, udid = await on_device(request, udid)
        instance = current.manager.instance(scope, udid)
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
    async def touch_status(request: Request, udid: str | None = Depends(which)) -> dict[str, Any]:
        """Whether the scope's real device -- or the one of its devices named -- can be touched, and what a person can
        do about it."""
        current, scope, udid = await on_device(request, udid)
        return ok(await current.touch.status(scope, udid))

    @router.post("/device/touch")
    async def touch_set_up(request: Request, udid: str | None = Depends(which)) -> dict[str, Any]:
        """Set touching the scope's real device -- or the one of its devices named -- up, as a person asked."""
        current, scope, udid = await on_device(request, udid)
        try:
            return ok(await current.touch.set_up(scope, udid))
        except TouchRefused as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/recordings")
    async def recording_list(request: Request, udid: str | None = Depends(which)) -> dict[str, Any]:
        """The scope's recordings kept, newest first, the one under way on its device -- or the one named -- and those
        still being rendered."""
        current, scope, udid = await on_device(request, udid)
        instance = current.manager.instance(scope, udid)
        running = instance.recording.state(current.manager.now()) if instance and instance.recording else None
        config = current.config.get(scope)
        listed = {"recordings": current.recordings.listing(config), "recording": running}
        return ok({**listed, "finishing": current.recordings.finishing(config)})

    @router.get("/recordings/{name}")
    async def recording_file(name: str, request: Request) -> FileResponse:
        """One kept recording's file, to watch or download: the scope's, whichever device it was recorded on."""
        current, scope = await asked(request)
        path = current.recordings.file(current.config.get(scope), name)
        if path is None:
            raise HTTPException(404, f"there is no recording {name!r}")
        return FileResponse(path, media_type=MEDIA_TYPES.get(path.suffix, "application/octet-stream"), filename=name)

    @router.post("/recording")
    async def recording_start(
        request: Request, body: RecordingAsked | None = None, udid: str | None = Depends(which)
    ) -> dict[str, Any]:
        """Start recording the scope's device -- or the one of its devices named -- as its settings say."""
        current, scope, udid = await on_device(request, udid)
        instance = current.manager.instance(scope, udid)
        if instance is None or instance.session is None:
            raise HTTPException(409, "the device is not running; start it first")
        config = current.config.get(scope)
        try:
            wanted = body or RecordingAsked()
            options = RecordingOptions.from_config(
                config,
                format=wanted.format,
                width=wanted.width,
                quality=wanted.quality,
                preset=wanted.preset,
                also=wanted.also,
                max_seconds=wanted.max_seconds,
                label=wanted.label,
                fps=wanted.fps,
            )
            control = current.manager.control(instance)
            await current.recordings.start(instance, config, control, by="person", options=options)
        except RecordingRefused as exc:
            raise HTTPException(409, str(exc)) from exc
        assert instance.recording is not None
        return ok({"recording": instance.recording.state(current.manager.now())})

    @router.delete("/recording")
    async def recording_stop(request: Request, udid: str | None = Depends(which)) -> dict[str, Any]:
        """Stop recording the scope's device -- or the one of its devices named -- and keep what was recorded, or answer
        the recording that stopped by itself."""
        current, scope, udid = await on_device(request, udid)
        instance = current.manager.instance(scope, udid)
        if instance is None or (instance.recording is None and current.recordings.ended(instance) is None):
            raise HTTPException(409, "nothing is being recorded")
        try:
            return ok(await current.recordings.stop(instance))
        except RecordingRefused as exc:
            raise HTTPException(409, str(exc)) from exc

    return router
