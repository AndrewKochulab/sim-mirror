# SPDX-License-Identifier: Apache-2.0
"""The viewer's pages and assets: served from the committed bundle, never cached, and honest when there is none."""

from __future__ import annotations

from pathlib import Path

import httpx
from fastapi import FastAPI

from sim_mirror.server.pages import NOT_BUILT, STATIC_VIEWER, create_page_router
from sim_mirror.testing.asgi import HOST


def served(static_dir: Path | None) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(create_page_router(static_dir))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=f"http://{HOST}")


async def test_the_viewer_and_embed_pages_and_their_assets_come_from_the_bundle(tmp_path: Path) -> None:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<p>viewer</p>")
    (tmp_path / "embed.html").write_text("<p>embed</p>")
    (tmp_path / "assets" / "viewer.js").write_text("export {}")
    async with served(tmp_path) as http:
        viewer = await http.get("/viewer/project-notes-1a2b3c4d")
        embed = await http.get("/embed/tp-1")
        asset = await http.get("/viewer-assets/viewer.js")
        odd_scope = await http.get("/viewer/bad%20scope")
        hidden = await http.get("/viewer-assets/.env")
        missing = await http.get("/viewer-assets/nope.js")
    assert (viewer.status_code, viewer.text, viewer.headers["cache-control"]) == (200, "<p>viewer</p>", "no-store")
    assert viewer.headers["content-type"].startswith("text/html") and embed.text == "<p>embed</p>"
    assert asset.status_code == 200 and asset.text == "export {}"
    assert odd_scope.status_code == 404 and "no such scope" in odd_scope.text
    assert hidden.status_code == 404 and missing.status_code == 404


async def test_an_install_without_a_bundle_says_so(tmp_path: Path) -> None:
    for static_dir in (None, tmp_path / "not-built"):
        async with served(static_dir) as http:
            page = await http.get("/viewer/tp-1")
            asset = await http.get("/viewer-assets/viewer.js")
        assert (page.status_code, page.text) == (503, NOT_BUILT) and asset.status_code == 404
    assert STATIC_VIEWER.parts[-2:] == ("static", "viewer")
