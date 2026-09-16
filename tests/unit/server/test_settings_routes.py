# SPDX-License-Identifier: Apache-2.0
"""The settings router a host mounts: every setting as a scope sees it, and a change checked whole, confirmed at the
terminal when it is sensitive, written, and acted on before the answer -- each asking the host who is there first."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from sim_mirror.config.provenance import SettingOrigin
from sim_mirror.core.runtime import Runtime
from sim_mirror.scope import Scope
from sim_mirror.seams import Refused, SettingsEditor, SettingsRefused
from sim_mirror.server.settings_routes import create_settings_router, digest_of
from sim_mirror.testing.asgi import HOST
from sim_mirror.testing.fakes import FakeConfirmations, MemorySettingsStore, no_wait
from sim_mirror.testing.rig import DeviceRig, scope

PREFIX = "/api/v1/scopes/{scope_id}/settings"
URL = "/api/v1/scopes/tp-1/settings"
DAEMON = Scope.named("daemon")
PROTOCOL = Path(__file__).resolve().parents[3] / "protocol" / "v1"
SCHEMAS = [json.loads(path.read_text()) for path in sorted(PROTOCOL.glob("*.schema.json"))]
REGISTRY: Registry[Any] = Registry().with_resources(
    (schema["$id"], Resource.from_contents(schema)) for schema in SCHEMAS
)
SETTINGS_ID = next(schema["$id"] for schema in SCHEMAS if schema["$id"].endswith("settings.schema.json"))


def conforms(definition: str, value: Any) -> None:
    Draft202012Validator({"$ref": f"{SETTINGS_ID}#/$defs/{definition}"}, registry=REGISTRY).validate(value)


class Keys:
    """A host's settings authenticator: what the asker may do is what the test says."""

    def __init__(self) -> None:
        self.may_write = True
        self.may_write_sensitive = False
        self.refusal: Refused | None = None

    async def settings_editor(self, request: Request, scope_id: str) -> SettingsEditor:
        if self.refusal is not None:
            raise self.refusal
        return SettingsEditor(scope(scope_id), self.may_write, self.may_write_sensitive)


@dataclass
class Served:
    rig: DeviceRig
    runtime: Runtime
    keys: Keys
    store: MemorySettingsStore
    confirmations: FakeConfirmations | None
    app: FastAPI = field(default_factory=FastAPI)
    running: bool = True
    reconciled: list[tuple[str | None, int]] = field(default_factory=list)

    def __post_init__(self) -> None:
        def source() -> Runtime | None:
            return self.runtime if self.running else None

        real = self.runtime.reconcile

        async def reconcile(group: str | None = None) -> None:
            # What had been written by the time the change was acted on.
            self.reconciled.append((group, len(self.store.changes)))
            await real(group)

        self.runtime.reconcile = reconcile  # type: ignore[method-assign]
        router = create_settings_router(
            source, self.store, self.keys, confirmations=self.confirmations, daemon_scope=DAEMON
        )
        self.app.include_router(router, prefix=PREFIX)

    async def call(self, method: str, body: Any = None) -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url=f"http://{HOST}") as http:
            return await http.request(method, URL, json=body)


def served(tmp_path: Path, *, confirmations: bool = True) -> Served:
    rig = DeviceRig(tmp_path)
    runtime = Runtime.build(
        config=rig.config,
        state=rig.state,
        memory=rig.memory,
        policy=rig.policy,
        copy=rig.copy,
        registry=rig.registry,
        claims=rig.claims,
        xcrun=rig.xcrun,
        clock=rig.clock,
        sleep=no_wait,
    )
    store = MemorySettingsStore(rig.config)
    return Served(rig, runtime, Keys(), store, FakeConfirmations() if confirmations else None)


def change(*pairs: tuple[str, Any], target: str = "scope", unset: tuple[str, ...] = (), code: str | None = None) -> Any:
    return {
        "target": target,
        "set": [{"path": path, "value": value} for path, value in pairs],
        "unset": list(unset),
        "confirmation": code,
    }


def entry(view: dict[str, Any], path: str) -> dict[str, Any]:
    return next(setting for setting in view["settings"] if setting["path"] == path)


async def test_every_setting_is_shown_as_the_scope_sees_it_and_the_daemons_own_as_the_daemon_does(
    tmp_path: Path,
) -> None:
    here = served(tmp_path)
    here.rig.config.set_for("tp-1", stream_fps=12)
    here.rig.config.set_for("daemon", server_port=7491)
    here.store.locked["stream_quality"] = SettingOrigin("environment", "SIM_MIRROR_STREAM_QUALITY")
    here.store.locked["max_booted"] = SettingOrigin("command_line")
    answer = await here.call("GET")
    assert answer.status_code == 200
    view = answer.json()["data"]
    conforms("SettingsView", view)
    assert view["scope"] == "tp-1" and view["access"] == "write"
    assert [section["title"] for section in view["sections"]][:3] == ["General", "Connectors", "Device"]
    fps = entry(view, "stream.fps")
    assert (fps["value"], fps["default"], fps["effect"], fps["reach"], fps["locked"]) == (12, 30, "live", "scope", None)
    assert fps["command"] == "sim-mirror config set stream.fps <value> --scope tp-1"
    port = entry(view, "server.port")
    assert (port["value"], port["sensitive"], port["reach"]) == (7491, True, "global")
    assert port["command"] == "sim-mirror config set server.port <value>"
    assert entry(view, "stream.quality")["locked"] == (
        "Set by SIM_MIRROR_STREAM_QUALITY in the daemon's environment, which config.toml cannot override: "
        "change it there."
    )
    assert entry(view, "device.max_booted")["locked"].startswith("Set by the daemon's command line")
    assert entry(view, "security.allowed_origins")["value"] == []
    here.keys.may_write, here.keys.may_write_sensitive = False, False
    assert (await here.call("GET")).json()["data"]["access"] == "read"
    here.keys.may_write, here.keys.may_write_sensitive = True, True
    assert (await here.call("GET")).json()["data"]["access"] == "write_sensitive"


async def test_a_change_is_written_for_the_scope_and_acted_on_before_the_answer(tmp_path: Path) -> None:
    here = served(tmp_path)
    answer = await here.call("PATCH", change(("stream.fps", 12), ("agent.cursor", False), unset=("stream.quality",)))
    assert answer.status_code == 200, answer.text
    view = answer.json()["data"]
    conforms("SettingsView", view)
    assert entry(view, "stream.fps")["value"] == 12 and entry(view, "stream.fps")["origin"]["layer"] == "scope"
    assert here.store.changes == [("tp-1", {"stream.fps": 12, "agent.cursor": False}, ["stream.quality"])]
    assert here.reconciled == [("alpha", 1)]
    everyone = await here.call("PATCH", change(("device.idle_minutes", 30), target="all"))
    assert everyone.status_code == 200 and here.store.changes[-1] == (None, {"device.idle_minutes": 30}, [])
    assert here.reconciled[-1] == (None, 2)


async def test_a_page_that_may_only_read_is_told_where_to_change_them_and_nothing_is_written(tmp_path: Path) -> None:
    here = served(tmp_path)
    here.keys.may_write = False
    answer = await here.call("PATCH", change(("stream.fps", 12)))
    assert answer.status_code == 403
    body = answer.json()
    conforms("SettingsRefusal", body)
    assert "open them with `sim-mirror open --settings`" in body["detail"] and here.store.changes == []


async def test_a_change_with_anything_wrong_names_each_problem_and_writes_nothing(tmp_path: Path) -> None:
    here = served(tmp_path)
    answer = await here.call(
        "PATCH", change(("stream.fps", 999), ("stream.zoom", 2), ("server.port", 7481), unset=("colour",))
    )
    assert answer.status_code == 422
    body = answer.json()
    conforms("SettingsRefusal", body)
    assert {problem["path"]: problem["message"] for problem in body["errors"]} == {
        "stream.fps": "stream.fps must be a whole number between 5 and 60",
        "stream.zoom": "stream.zoom is not a setting",
        "server.port": "server.port applies to the whole daemon, so it cannot be set for one scope; "
        "set it without a scope",
        "colour": "colour is not a setting",
    }
    assert here.store.changes == [] and here.reconciled == []


async def test_a_setting_something_above_the_file_sets_is_refused_since_writing_it_would_change_nothing(
    tmp_path: Path,
) -> None:
    here = served(tmp_path)
    here.store.locked["stream_fps"] = SettingOrigin("environment", "SIM_MIRROR_STREAM_FPS")
    answer = await here.call("PATCH", change(("stream.fps", 12), ("stream.quality", 60)))
    assert answer.status_code == 409
    assert [problem["path"] for problem in answer.json()["errors"]] == ["stream.fps"]
    assert "SIM_MIRROR_STREAM_FPS in the daemon's environment" in answer.json()["detail"]
    here.store.locked = {"server_port": SettingOrigin("command_line")}
    assert (await here.call("PATCH", change(("server.port", 7481), target="all"))).status_code == 409
    assert here.store.changes == []


async def test_a_sensitive_change_waits_for_the_code_shown_at_the_terminal_and_that_code_confirms_only_it(
    tmp_path: Path,
) -> None:
    here = served(tmp_path)
    assert here.confirmations is not None
    wanted = change(("build.tools", True), ("stream.fps", 12))
    first = await here.call("PATCH", wanted)
    assert first.status_code == 428 and here.store.changes == []
    body = first.json()
    conforms("SettingsRefusal", body)
    assert body["detail"] == (
        "Changing build.tools needs a person at the terminal: run `sim-mirror settings confirm` "
        "and enter the code it shows for this change."
    )
    assert body["confirmation"]["id"] == "change-1"
    assert here.confirmations.requests[0][2] == "tp-1: build.tools = true; stream.fps = 12"
    (digest, code), = here.confirmations.codes.items()  # fmt: skip
    # The code is for exactly that change: a different one with the same code is held again, and the code still waits.
    other = await here.call("PATCH", change(("build.tools", True), code=code))
    assert other.status_code == 428 and other.json()["detail"].startswith("That code does not confirm this change")
    wrong = await here.call("PATCH", {**wanted, "confirmation": "nope"})
    assert wrong.status_code == 428 and wrong.json()["confirmation"]["id"] == "change-1"
    confirmed = await here.call("PATCH", {**wanted, "confirmation": code})
    assert confirmed.status_code == 200 and here.store.changes == [
        ("tp-1", {"build.tools": True, "stream.fps": 12}, [])
    ]
    assert digest not in here.confirmations.codes
    again = await here.call("PATCH", {**wanted, "confirmation": code})
    assert again.status_code == 428, "a code is spent once"


async def test_one_who_may_change_sensitive_settings_needs_no_code_and_without_confirmations_a_page_is_refused(
    tmp_path: Path,
) -> None:
    trusted = served(tmp_path / "trusted")
    trusted.keys.may_write_sensitive = True
    assert (await trusted.call("PATCH", change(unset=("device.developer_dir",)))).status_code == 200
    assert trusted.confirmations is not None and trusted.confirmations.requests == []
    alone = served(tmp_path / "alone", confirmations=False)
    answer = await alone.call("PATCH", change(("security.allowed_origins", ["http://localhost:3000"]), target="all"))
    assert answer.status_code == 403
    assert (
        answer.json()["detail"] == "security.allowed_origins can only be changed at the terminal (`sim-mirror config`)."
    )


async def test_a_store_that_refuses_and_a_host_that_refuses_are_both_said(tmp_path: Path) -> None:
    here = served(tmp_path)
    here.store.refusal = SettingsRefused({"config.toml": "cannot edit config.toml, which is not valid TOML"})
    answer = await here.call("PATCH", change(("stream.fps", 12)))
    assert answer.status_code == 422 and answer.json()["errors"][0]["path"] == "config.toml"
    assert here.reconciled == []
    here.keys.refusal = Refused(404, "there is no such scope")
    assert (await here.call("GET")).status_code == 404
    here.keys.refusal = None
    here.running = False
    assert (await here.call("GET")).status_code == 503 and (await here.call("PATCH", change())).status_code == 503


def test_a_changes_digest_is_its_content_whatever_the_order_it_was_sent_in() -> None:
    from sim_mirror.server.settings_routes import SettingsChangeBody

    one = SettingsChangeBody.model_validate(change(("stream.fps", 12), ("build.tools", True), unset=("a", "b")))
    two = SettingsChangeBody.model_validate(change(("build.tools", True), ("stream.fps", 12), unset=("b", "a")))
    everyone = SettingsChangeBody.model_validate(change(("build.tools", True), ("stream.fps", 12), target="all"))
    assert digest_of(scope(), one) == digest_of(scope(), two) != digest_of(scope("tp-2"), one)
    assert digest_of(scope(), everyone) != digest_of(scope(), SettingsChangeBody.model_validate(change(
        ("build.tools", True), ("stream.fps", 12))))  # fmt: skip
