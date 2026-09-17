# SPDX-License-Identifier: Apache-2.0
"""A scope's settings, read and changed by the viewer's settings panel.

A host that lets a page change settings mounts this router under a scope's prefix; one that keeps its own settings
screens does not mount it, and nothing else changes.

* ``GET ""`` -- every setting as the scope sees it, and what the asker may do (`SettingsView`);
* ``PATCH ""`` -- a `SettingsChange`: values set and settings put back, for this scope or for every scope, all or none.
  It answers the new view once the change has been acted on -- a device switched off is off before the answer.

A change is refused, before anything is written, with each setting's problem (`SettingsRefusal`):

* 403 for someone who may only read, and for a sensitive setting where nothing can confirm it;
* 422 for a setting that is not one, a value its rule refuses, or one only the whole daemon reads, set for a scope;
* 409 for a setting something above config.toml sets -- a variable, the command line -- since writing it changes
  nothing;
* 428 for a sensitive setting (`Setting.sensitive`) the asker may not change alone: the change waits for a person to
  confirm it somewhere a page cannot reach (`Confirmations`), and is sent again with the code they were shown. The code
  is bound to the change's digest, so it confirms exactly the change that was shown and nothing else.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from sim_mirror.config.writer import ConfigRefused, plan_change
from sim_mirror.core.runtime import Runtime
from sim_mirror.protocol import PendingConfirmation, SettingsView
from sim_mirror.scope import Scope
from sim_mirror.seams import (
    SETTINGS_REFUSED,
    Confirmations,
    Refused,
    SettingsAuthenticator,
    SettingsEditor,
    SettingsRefused,
    SettingsStore,
)
from sim_mirror.server.envelope import RuntimeSource, ok, running
from sim_mirror.server.http_routes import SCOPE_PARAM
from sim_mirror.server.settings_view import locked_reason, settings_view

#: The most settings one change may name; there are fewer settings than this.
CHANGE_MAX = 64


class ValueChange(BaseModel):
    path: str = Field(min_length=1, max_length=100)
    value: bool | int | str | list[str]


class SettingsChangeBody(BaseModel):
    target: Literal["scope", "all"]
    set: list[ValueChange] = Field(default_factory=list, max_length=CHANGE_MAX)
    unset: list[str] = Field(default_factory=list, max_length=CHANGE_MAX)
    confirmation: str | None = Field(default=None, max_length=128)


def refusal(
    status: int,
    detail: str,
    errors: Mapping[str, str] | None = None,
    confirmation: PendingConfirmation | None = None,
) -> JSONResponse:
    problems = [{"path": path, "message": message} for path, message in (errors or {}).items()]
    return JSONResponse({"detail": detail, "errors": problems, "confirmation": confirmation}, status_code=status)


def unprocessable(errors: Mapping[str, str]) -> JSONResponse:
    """422 for a change refused with each setting's problem. It says those problems and nothing else of the refusal:
    a host's store raises `SettingsRefused`, and an exception's own text is not the page's to read."""
    problems = {str(path): str(message) for path, message in errors.items()}
    return refusal(422, next(iter(problems.values()), SETTINGS_REFUSED), problems)


def digest_of(scope: Scope, body: SettingsChangeBody) -> str:
    """What a confirmation is bound to: the scope, the target and exactly the values and settings it changes."""
    change = {
        "scope": scope.id,
        "target": body.target,
        "set": sorted(([item.path, item.value] for item in body.set), key=lambda pair: str(pair[0])),
        "unset": sorted(body.unset),
    }
    return hashlib.sha256(json.dumps(change, sort_keys=True).encode("utf-8")).hexdigest()


def listed(paths: Sequence[str]) -> str:
    return ", ".join(paths)


def create_settings_router(
    runtime: RuntimeSource,
    store: SettingsStore,
    auth: SettingsAuthenticator,
    *,
    confirmations: Confirmations | None = None,
    daemon_scope: Scope | None = None,
    scope_param: str = SCOPE_PARAM,
) -> APIRouter:
    """`daemon_scope` is the scope the host reads its own settings as -- those only the whole daemon has, such as its
    port; without one, a scope's own settings stand for them."""
    router = APIRouter()

    async def editor_for(request: Request) -> SettingsEditor:
        try:
            return await auth.settings_editor(request, str(request.path_params.get(scope_param, "")))
        except Refused as exc:
            raise HTTPException(exc.status, exc.message) from exc

    def view(current: Runtime, editor: SettingsEditor) -> SettingsView:
        daemon = daemon_scope or editor.scope
        return settings_view(
            editor,
            scoped=current.config.get(editor.scope),
            daemon=current.config.get(daemon),
            scoped_origins=store.explain(editor.scope),
            daemon_origins=store.explain(daemon),
            copy=current.copy,
            suggestions={"connectors.preferred": ["auto", *current.registry.names()]},
        )

    @router.get("")
    async def settings_read(request: Request) -> dict[str, Any]:
        """Every setting as the scope sees it, and what the asker may do."""
        editor = await editor_for(request)
        return ok(view(running(runtime), editor))

    @router.patch("", response_model=None)
    async def settings_change(body: SettingsChangeBody, request: Request) -> dict[str, Any] | JSONResponse:
        """Set values and put settings back, all or none, then act on them before answering."""
        editor = await editor_for(request)
        current = running(runtime)
        copy = current.copy
        if not editor.may_write:
            return refusal(403, copy.settings_read_only())
        scoped = body.target == "scope"
        if not scoped and not editor.may_write_every_scope:
            return refusal(403, copy.settings_this_scope_only())
        try:
            plan = plan_change({item.path: item.value for item in body.set}, body.unset, scoped=scoped)
        except ConfigRefused as exc:
            return unprocessable(exc.errors)
        named = [*(setting for setting, _value in plan.set.values()), *plan.unset]
        origins = {
            False: store.explain(editor.scope),
            True: store.explain(daemon_scope or editor.scope),
        }
        reasons = {
            setting.path: locked_reason(origins[setting.reach == "global"][setting.key], copy) for setting in named
        }
        locked = {path: reason for path, reason in reasons.items() if reason is not None}
        if locked:
            return refusal(409, next(iter(locked.values())), locked)
        sensitive = [setting.path for setting in named if setting.sensitive]
        if sensitive and not editor.may_write_sensitive:
            if confirmations is None:
                return refusal(403, copy.settings_need_terminal(listed(sensitive)))
            digest = digest_of(editor.scope, body)
            if body.confirmation is None or not confirmations.confirm(digest, body.confirmation):
                summary = f"{editor.scope.id}: " + "; ".join(
                    [f"{item.path} = {json.dumps(item.value)}" for item in body.set]
                    + [f"{path} put back" for path in body.unset]
                )
                pending = confirmations.request(editor.scope, digest, summary)
                said = copy.settings_confirm(listed(sensitive))
                if body.confirmation is not None:
                    said = f"{copy.settings_code_wrong()} {said}"
                return refusal(428, said, confirmation=pending)
        try:
            await asyncio.to_thread(
                store.change,
                editor.scope if scoped else None,
                {path: value for path, (_setting, value) in plan.set.items()},
                [setting.path for setting in plan.unset],
            )
        except SettingsRefused as exc:
            return unprocessable(exc.errors)
        await current.reconcile(editor.scope.group if scoped else None)
        return ok(view(current, editor))

    return router
