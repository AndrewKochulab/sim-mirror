# SPDX-License-Identifier: Apache-2.0
"""The FastAPI embed example: a host's seams and routers work together, over a fake Mac."""

from __future__ import annotations

import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import app as example
import httpx
import pytest

from sim_mirror.api import Admission, Refused, Runtime, Scope
from sim_mirror.testing.asgi import HOST
from sim_mirror.testing.rig import DeviceRig

KEY = "change-me"


def runtime(root: Path) -> Runtime:
    rig = DeviceRig(root)
    return Runtime.build(
        config=example.ExampleConfig(),
        state=example.ExampleState(root / "state", claims=root / "claims"),
        policy=example.ExamplePolicy(),
        registry=rig.registry,
        claims=rig.claims,
        xcrun=rig.xcrun,
        platform="darwin",
    )


def client(application: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url=f"http://{HOST}")


async def test_a_person_with_the_key_reaches_a_projects_simulator_and_no_one_else(tmp_path: Path) -> None:
    async with client(example.create_app(runtime(tmp_path), key=KEY)) as http:
        status = await http.get("/api/projects/demo/simulator", headers={"X-Example-Key": KEY})
        keyless = await http.get("/api/projects/demo/simulator")
        wrong = await http.get("/api/projects/demo/simulator", headers={"X-Example-Key": "guess"})
        odd = await http.get("/api/projects/bad%20id/simulator", headers={"X-Example-Key": KEY})
    assert status.status_code == 200 and status.json()["ok"] is True
    assert (keyless.status_code, wrong.status_code) == (401, 401)
    assert keyless.json()["detail"] == "authentication required"
    assert odd.status_code == 404


async def test_an_agent_names_its_project_and_is_given_the_tools(tmp_path: Path) -> None:
    headers = {"X-Example-Key": KEY, "X-Example-Project": "demo", "X-Example-Client": "  My   Agent "}
    async with client(example.create_app(runtime(tmp_path), key=KEY)) as http:
        manifest = await http.get("/api/agent/simulator/manifest", headers=headers)
        unnamed = await http.get("/api/agent/simulator/manifest", headers={"X-Example-Key": KEY})
    assert manifest.status_code == 200
    assert "sim_snapshot" in [tool["name"] for tool in manifest.json()["tools"]]
    assert unnamed.status_code == 404


async def test_the_seams_say_what_this_host_decided(tmp_path: Path) -> None:
    scope = Scope(id="demo", group=example.GROUP, label="demo")
    state = example.ExampleState(tmp_path / "state", claims=tmp_path / "claims")
    assert state.owner_tag == "SimMirrorExample"
    assert state.devices_file(scope) == tmp_path / "state" / "devices.json"
    assert state.builds_dir(scope) == tmp_path / "state" / "builds" / "demo"
    assert state.derived_data(scope) == tmp_path / "state" / "DerivedData" / "demo"
    assert (state.run_dir(), state.log_dir(), state.claims_dir()) == (
        tmp_path / "state" / "run",
        tmp_path / "state" / "logs",
        tmp_path / "claims",
    )
    made = state.ensure_dir(tmp_path / "state" / "private")
    assert stat.S_IMODE(made.stat().st_mode) == 0o700
    policy = example.ExamplePolicy()
    assert policy.area_enabled(scope) and not policy.shells_allowed(scope)
    assert policy.install_roots(scope) == () and policy.build_folder(scope) is None
    assert example.ExampleConfig().get(scope).enabled is True
    socket: Any = object()
    assert await example.SharedKey(KEY).admit_socket(socket, "demo") == Admission(scope)
    keyless: Any = SimpleNamespace(headers={example.KEY_HEADER: ""})
    with pytest.raises(Refused, match="authentication required"):
        await example.SharedKey("").person(keyless, "demo")


async def test_simulators_start_and_stop_with_the_application(tmp_path: Path) -> None:
    simulators = runtime(tmp_path)
    application = example.create_app(simulators, key=KEY)
    async with application.router.lifespan_context(application):
        assert simulators.reaper is not None
    assert isinstance(
        example.build_runtime(example.ExampleState(tmp_path / "own", claims=tmp_path / "claims")), Runtime
    )


def test_the_key_comes_from_the_environment_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(example.KEY_ENV, "from-env")
    application = example.create_app(runtime(tmp_path))
    assert application.title == "SimMirror embedded in an application"
