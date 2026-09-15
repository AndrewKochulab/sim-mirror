# SPDX-License-Identifier: Apache-2.0
"""The viewer's pages: ``/viewer/<scope>`` for a browser tab, ``/embed/<scope>`` for a frame, and their assets.

Both are the committed standalone viewer bundle (`server/static/viewer/`), so an install from a git tag needs no Node.
A page gets in with the code or ticket in its URL's fragment (`daemon.passes`), which never reaches the server. Pages
are never cached -- a new install's viewer is the one shown -- and an install without a bundle says so rather than
serving a blank page.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, HTMLResponse, Response

from sim_mirror.scope import ID_PATTERN

STATIC_VIEWER = Path(__file__).parent / "static" / "viewer"
VIEWER_PAGE = "index.html"
EMBED_PAGE = "embed.html"
ASSETS = "assets"
NO_STORE = {"Cache-Control": "no-store"}
NOT_BUILT = (
    "<!doctype html><meta charset=utf-8><title>SimMirror</title>"
    "<p>This install of SimMirror has no viewer built into it.</p>"
)
NO_SUCH_SCOPE = "<!doctype html><meta charset=utf-8><title>SimMirror</title><p>There is no such scope.</p>"

_ASSET_NAME = re.compile(r"\A[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}\Z")


def create_page_router(static_dir: Path | None = STATIC_VIEWER) -> APIRouter:
    router = APIRouter()

    def page(name: str, scope_id: str) -> Response:
        if not ID_PATTERN.match(scope_id):
            return HTMLResponse(NO_SUCH_SCOPE, status_code=404, headers=NO_STORE)
        path = static_dir / name if static_dir is not None else None
        if path is None or not path.is_file():
            return HTMLResponse(NOT_BUILT, status_code=503, headers=NO_STORE)
        return FileResponse(path, media_type="text/html", headers=NO_STORE)

    @router.get("/viewer/{scope_id}")
    async def viewer_page(scope_id: str) -> Response:
        return page(VIEWER_PAGE, scope_id)

    @router.get("/embed/{scope_id}")
    async def embed_page(scope_id: str) -> Response:
        return page(EMBED_PAGE, scope_id)

    @router.get("/viewer-assets/{name}")
    async def viewer_asset(name: str) -> Response:
        path = static_dir / ASSETS / name if static_dir is not None and _ASSET_NAME.match(name) else None
        if path is None or not path.is_file():
            return Response(status_code=404)
        return FileResponse(path)

    return router
