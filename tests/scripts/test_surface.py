# SPDX-License-Identifier: Apache-2.0
"""The 1.x promise: the code keeps `compat/surface-v1.json`, and every kind of break is named -- while an addition is
not one."""

from __future__ import annotations

import argparse
import io
import json
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Protocol

import pytest

import surface

# -- the promise itself -----------------------------------------------------------------------------------------------


def test_the_code_keeps_every_promise_of_1x() -> None:
    out = io.StringIO()
    assert surface.main([], out=out) == 0, out.getvalue()


def test_the_promise_is_written_once_and_a_break_is_named_with_where_to_read_why(tmp_path: Path) -> None:
    promise = tmp_path / "surface-v1.json"
    before = {"cli": {"sim-mirror go": {"options": {"--x": False}, "positionals": []}}}
    out = io.StringIO()
    assert surface.main(["--write", str(promise)], current=lambda: before, out=out) == 0
    assert out.getvalue() == f"wrote {promise}\n" and json.loads(promise.read_text()) == before

    after = {"cli": {"sim-mirror go": {"options": {}, "positionals": []}}}
    out = io.StringIO()
    assert surface.main([], promise=promise, current=lambda: after, out=out) == 1
    assert out.getvalue() == "cli sim-mirror go --x: is gone\n1 break(s) of surface-v1.json: see docs/stability.md\n"


def test_an_area_the_code_no_longer_describes_is_a_break_and_one_never_promised_is_not_checked() -> None:
    assert surface.breaks({area: {} for area in surface.AREAS}, {}) == [f"{area}: is gone" for area in surface.AREAS]
    assert surface.breaks({"api": {}}, {"api": {}}) == []


# -- the Python API ---------------------------------------------------------------------------------------------------


def _module(**names: Any) -> ModuleType:
    module = ModuleType("sample")
    module.__all__ = sorted(names)  # type: ignore[attr-defined]
    for name, value in names.items():
        setattr(module, name, value)
    return module


def P(name: str, kind: str = "either", default: bool = False) -> list[Any]:
    return [name, kind, default]


@pytest.mark.parametrize(
    ("old", "new", "said"),
    [
        ([P("a"), P("b")], [P("a"), P("b"), P("c", "keyword", True)], []),
        ([P("a", "keyword")], [P("a", "either")], []),
        ([P("a"), P("b")], [P("b"), P("a")], ["f: its positional parameters were a, b; now b, a"]),
        ([P("a", "keyword")], [], ["f: lost the parameter a"]),
        ([P("a"), P("b")], [P("b")], ["f: lost the parameter a"]),
        (
            [P("a")],
            [P("z"), P("a")],
            ["f: its positional parameters were a; now z, a", "f: the new parameter z must be given"],
        ),
        (
            [P("a")],
            [P("a", "keyword")],
            ["f: a can no longer be passed by position"],
        ),
        ([P("a")], [P("a", "positional")], ["f: a can no longer be passed by name"]),
        ([P("a", "keyword", True)], [P("a", "keyword")], ["f: a must now be given"]),
        ([], [P("b", "keyword")], ["f: the new parameter b must be given"]),
        ([P("rest", "**kwargs")], [], ["f: no longer takes **kwargs"]),
        ([P("rest", "*args")], [P("rest", "*args"), P("more", "**kwargs")], []),
    ],
)
def test_a_call_that_worked_keeps_working(old: list[list[Any]], new: list[list[Any]], said: list[str]) -> None:
    assert surface._call_breaks("f", old, new) == said


def test_names_are_described_by_what_a_host_does_with_them() -> None:
    class Seam(Protocol):
        @property
        def tag(self) -> str: ...

        def get(self, scope: str) -> str: ...

    @dataclass(frozen=True)
    class Record:
        scope: str
        may: bool = False

    class Failure(Exception):
        def __init__(self, status: int, message: str) -> None:
            super().__init__(message)
            self.status = status
            self._hidden = message

    class Thing:
        def __init__(self, url: str) -> None:
            self.url: str = url

        @classmethod
        def made(cls, *, url: str) -> Thing:
            return cls(url)

        @staticmethod
        def pure(value: int) -> int:
            return value

        def call(self, name: str) -> str:
            return name

        @property
        def ready(self) -> bool:
            return True

        def _private(self) -> None: ...

    def factory(runtime: object, *, prefix: str = "") -> None: ...

    described = surface.describe_api(
        _module(Seam=Seam, Record=Record, Failure=Failure, Thing=Thing, factory=factory, Plain=LookupError)
    )
    assert described["Seam"] == {"kind": "seam", "methods": {"get": [P("scope")], "tag": "property"}}
    assert described["Record"]["init"] == [P("scope"), P("may", default=True)]
    assert described["Record"]["attributes"] == ["may", "scope"]
    assert described["Failure"] == {
        "kind": "class",
        "attributes": ["status"],
        "methods": {},
        "init": [P("status"), P("message")],
    }
    assert described["Thing"]["methods"] == {
        "call": [P("name")],
        "made": [P("url", "keyword")],
        "pure": [P("value")],
        "ready": "property",
    }
    assert described["Thing"]["attributes"] == ["url"]
    assert described["factory"] == {"kind": "function", "params": [P("runtime"), P("prefix", "keyword", True)]}
    assert described["Plain"]["init"] == [P("args", "*args")]


def test_what_a_runtime_and_settings_are_made_by_and_what_is_left_to_simmirrors_own_tests() -> None:
    from sim_mirror import api

    described = surface.describe_api(api)
    runtime = described["Runtime"]
    assert "init" not in runtime and runtime["attributes"] == []
    assert set(runtime["methods"]) == {"build", "start", "close", "reconcile", "refusal", "manifest", "call"}
    assert [name for name, _, _ in runtime["methods"]["build"]] == [
        "config", "state", "policy", "memory", "copy", "usage", "may_share",
    ]  # fmt: skip
    assert "init" not in described["SimConfig"] and "stream_fps" in described["SimConfig"]["attributes"]


CLASS = {
    "kind": "class",
    "attributes": ["url"],
    "methods": {"call": [P("name")], "ready": "property"},
    "init": [P("url")],
}


@pytest.mark.parametrize(
    ("now", "said"),
    [
        ({"Thing": CLASS | {"methods": CLASS["methods"] | {"more": []}, "attributes": ["url", "extra"]}}, []),
        ({}, ["api Thing: is no longer on sim_mirror.api"]),
        ({"Thing": {"kind": "function", "params": []}}, ["api Thing: was a class, now a function"]),
        ({"Thing": {k: v for k, v in CLASS.items() if k != "init"}}, ["api Thing: can no longer be made directly"]),
        ({"Thing": CLASS | {"init": [P("url"), P("token")]}}, ["api Thing: the new parameter token must be given"]),
        ({"Thing": CLASS | {"attributes": []}}, ["api Thing.url: is gone"]),
        ({"Thing": CLASS | {"attributes": [], "methods": CLASS["methods"] | {"url": "property"}}}, []),
        ({"Thing": CLASS | {"methods": {"ready": "property"}}}, ["api Thing.call: is gone"]),
        ({"Thing": CLASS | {"methods": {"call": [P("name")]}, "attributes": ["url", "ready"]}}, []),
        (
            {"Thing": CLASS | {"methods": {"call": "property", "ready": "property"}}},
            ["api Thing.call: was a method, now not"],
        ),
        (
            {"Thing": CLASS | {"methods": {"call": [P("name")], "ready": []}}},
            ["api Thing.ready: was a property, now not"],
        ),
        (
            {"Thing": CLASS | {"methods": {"call": [], "ready": "property"}}},
            ["api Thing.call: lost the parameter name"],
        ),
    ],
)
def test_a_class_keeps_how_it_is_made_what_it_holds_and_what_it_does(now: dict[str, Any], said: list[str]) -> None:
    assert surface.api_breaks({"Thing": CLASS}, now) == said


def test_a_seam_a_host_implements_never_gains_a_method_or_changes_a_call_and_a_function_keeps_its_parameters() -> None:
    seam = {"kind": "seam", "methods": {"get": [P("scope")], "old": []}}
    assert surface.api_breaks({"S": seam}, {"S": {"kind": "seam", "methods": {"get": [P("scope")]}}}) == []
    changed = {"kind": "seam", "methods": {"get": [P("scope"), P("fresh", "keyword", True)], "put": []}}
    assert surface.api_breaks({"S": seam}, {"S": changed}) == [
        "api S: a host implementing it must now write put too",
        "api S.get: what SimMirror calls it with changed, and a host's has not",
    ]
    function = {"kind": "function", "params": [P("a")]}
    assert surface.api_breaks({"f": function}, {"f": {"kind": "function", "params": []}}) == [
        "api f: lost the parameter a",
    ]


# -- the protocol and the tools ---------------------------------------------------------------------------------------


def _protocol(tmp_path: Path, schemas: dict[str, Any], constants: dict[str, Any], codes: dict[str, int]) -> Any:
    folder = tmp_path / "v1"
    folder.mkdir(exist_ok=True)
    for old in folder.glob("*.json"):
        old.unlink()
    for name, schema in schemas.items():
        (folder / name).write_text(json.dumps(schema))
    (folder / "constants.json").write_text(json.dumps({"$comment": "c", **constants}))
    (folder / "close-codes.json").write_text(
        json.dumps({"close_codes": {key: {"code": code, "description": "d"} for key, code in codes.items()}})
    )
    return surface.describe_protocol(folder)


def _object(properties: dict[str, Any], required: list[str] | None = None, **more: Any) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else required,
        **more,
    }


BASE = {
    "common.schema.json": {
        "description": "shared",
        "$defs": {
            "Share": {"type": "number", "minimum": 0, "maximum": 1, "description": "d"},
            "Kind": {"enum": ["a", "b"], "x-constant": "KINDS"},
        },
    },
    "client-input.schema.json": {
        "oneOf": [{"$ref": "#/$defs/Touch"}],
        "$defs": {
            "Touch": _object(
                {"nx": {"$ref": "common.schema.json#/$defs/Share"}, "text": {"type": "string", "maxLength": 10}}
            )
        },
    },
    "hello.schema.json": {
        "$defs": {
            "ClientHello": _object({"v": {"const": 1}, "encodings": {"type": "array", "items": {"type": "string"}}}),
            "ServerHello": _object(
                {
                    "v": {"const": 1},
                    "kind": {"$ref": "common.schema.json#/$defs/Kind"},
                    "note": {"type": ["string", "null"]},
                }
            ),
        }
    },
    "agent-event.schema.json": {
        "$defs": {
            "Point": {"type": "array", "prefixItems": [{"$ref": "common.schema.json#/$defs/Share"}], "items": False}
        }
    },
}


def _with(file: str, name: str, change: Any) -> dict[str, Any]:
    schemas = json.loads(json.dumps(BASE))
    change(schemas[file]["$defs"][name] if name else schemas[file])
    return schemas


@pytest.mark.parametrize(
    ("schemas", "said"),
    [
        (
            _with(
                "hello.schema.json",
                "ServerHello",
                lambda d: d["properties"].update(extra={"type": "string"}) or d["required"].append("extra"),
            ),
            [],
        ),
        (_with("hello.schema.json", "ClientHello", lambda d: d["properties"].update(extra={"type": "string"})), []),
        (_with("common.schema.json", "Kind", lambda d: d["enum"].append("c")), []),
        (_with("client-input.schema.json", "", lambda d: d["oneOf"].append({"$ref": "#/$defs/Touch"})), []),
        (
            _with("common.schema.json", "Kind", lambda d: d["enum"].remove("b")),
            ["protocol common.schema.json Kind: lost b"],
        ),
        (
            _with("common.schema.json", "Kind", lambda d: d.update({"x-constant": "SORTS"})),
            ['protocol common.schema.json Kind: its x-constant was "KINDS", now "SORTS"'],
        ),
        (
            _with("hello.schema.json", "ServerHello", lambda d: d["required"].remove("note")),
            ["protocol hello.schema.json ServerHello: no longer always sends note"],
        ),
        (
            _with(
                "hello.schema.json",
                "ServerHello",
                lambda d: d["properties"]["note"].update(type=["string", "null", "integer"]),
            ),
            ["protocol hello.schema.json ServerHello.note: may now be integer"],
        ),
        (
            _with("hello.schema.json", "ServerHello", lambda d: d["properties"].pop("note")),
            ["protocol hello.schema.json ServerHello.note: is gone"],
        ),
        (
            _with(
                "hello.schema.json",
                "ClientHello",
                lambda d: d["properties"].update(extra={"type": "string"}) or d["required"].append("extra"),
            ),
            ["protocol hello.schema.json ClientHello: now requires extra"],
        ),
        (
            _with(
                "hello.schema.json",
                "ClientHello",
                lambda d: d["properties"]["encodings"]["items"].update(type="integer"),
            ),
            ["protocol hello.schema.json ClientHello.encodings[]: no longer accepts string"],
        ),
        (
            _with("hello.schema.json", "ClientHello", lambda d: d["properties"]["v"].update(const=2)),
            ["protocol hello.schema.json ClientHello.v: its const was 1, now 2"],
        ),
        (
            _with("client-input.schema.json", "Touch", lambda d: d["properties"]["text"].update(maxLength=5)),
            ["protocol client-input.schema.json Touch.text: its maxLength tightened to 5"],
        ),
        (
            _with("client-input.schema.json", "Touch", lambda d: d["properties"]["text"].update(minLength=1)),
            ["protocol client-input.schema.json Touch.text: its minLength tightened to 1"],
        ),
        (
            _with("client-input.schema.json", "Touch", lambda d: d.update(additionalProperties=False)),
            ["protocol client-input.schema.json Touch: no longer accepts properties it does not describe"],
        ),
        (
            _with("client-input.schema.json", "", lambda d: d.update(oneOf=[])),
            ["protocol client-input.schema.json: its oneOf had 1, now 0"],
        ),
        # A share of the screen is read from a client and sent to one: checked both ways.
        (
            _with("common.schema.json", "Share", lambda d: d.update(maximum=0.5)),
            ["protocol common.schema.json Share: its maximum tightened to 0.5"],
        ),
        (
            _with("common.schema.json", "Share", lambda d: d.update(type=["number", "null"])),
            ["protocol common.schema.json Share: may now be null"],
        ),
        (
            _with("agent-event.schema.json", "Point", lambda d: d["prefixItems"][0].update({"$ref": "#/$defs/Other"})),
            [
                "protocol agent-event.schema.json Point.prefixItems[0]: its $ref was "
                '"common.schema.json#/$defs/Share", now "#/$defs/Other"'
            ],
        ),
        (
            _with("agent-event.schema.json", "", lambda d: d["$defs"].pop("Point")),
            ["protocol agent-event.schema.json Point: is gone"],
        ),
        (
            {k: v for k, v in BASE.items() if k != "agent-event.schema.json"},
            ["protocol agent-event.schema.json: is gone"],
        ),
    ],
)
def test_a_message_keeps_what_its_receivers_rely_on(tmp_path: Path, schemas: dict[str, Any], said: list[str]) -> None:
    promised = _protocol(tmp_path, BASE, {"tag": 1}, {"gone": 4410})
    assert surface.protocol_breaks(promised, _protocol(tmp_path, schemas, {"tag": 1}, {"gone": 4410})) == said


def test_the_constants_and_close_codes_never_change_and_a_value_that_is_not_a_schema_is_compared_whole(
    tmp_path: Path,
) -> None:
    promised = _protocol(tmp_path, BASE, {"tag": 1, "limit": 10}, {"gone": 4410})
    now = _protocol(tmp_path, BASE, {"tag": 2, "more": 3}, {"gone": 4411, "new": 4500})
    assert surface.protocol_breaks(promised, now) == [
        "protocol constants tag: was 1, now 2",
        "protocol constants limit: was 10, now None",
        "protocol close_codes gone: was 4410, now 4411",
    ]
    assert surface._node_breaks("x", True, True, inbound=True, outbound=True) == []
    assert surface._node_breaks("x", True, {"type": "object"}, inbound=True, outbound=True) == [
        'x: was true, now {"type": "object"}'
    ]
    assert surface._node_breaks("x", {"type": "string"}, False, inbound=True, outbound=True) == ["x: is gone"]


def test_the_directions_are_read_from_the_promise() -> None:
    inbound, outbound = surface.directions(BASE)
    assert ("common.schema.json", "Share") in inbound and ("common.schema.json", "Share") in outbound
    assert ("hello.schema.json", "ClientHello") in inbound and ("hello.schema.json", "ClientHello") not in outbound
    assert ("common.schema.json", "Kind") in outbound and ("common.schema.json", "Kind") not in inbound


def test_a_tool_keeps_its_arguments_and_sim_act_its_steps() -> None:
    arguments = {"sim_act": ("d", _object({"steps": {"type": "array", "maxItems": 20}}, ["steps"]))}
    promised = surface.describe_tools(arguments, {"tap": (), "type": ("into", "clear")})
    assert promised["steps"] == {"tap": [], "type": ["clear", "into"]}
    assert (
        surface.tool_breaks(
            promised, surface.describe_tools(arguments, {"tap": (), "type": ("into", "clear", "submit"), "pause": ()})
        )
        == []
    )
    narrower = {
        "sim_act": (
            "d",
            _object({"steps": {"type": "array", "maxItems": 10}, "wait": {"type": "object"}}, ["steps", "wait"]),
        )
    }
    assert surface.tool_breaks(promised, surface.describe_tools(narrower, {"type": ("into",)})) == [
        "tool sim_act.steps: its maxItems tightened to 10",
        "tool sim_act: now requires wait",
        "tool sim_act step tap: is gone",
        "tool sim_act step type: no longer takes clear",
    ]
    assert surface.tool_breaks(promised, surface.describe_tools({}, {"tap": (), "type": ("into", "clear")})) == [
        "tool sim_act: is gone"
    ]


# -- settings and the command line ------------------------------------------------------------------------------------


def _setting(path: str, spec: dict[str, Any], reach: str = "scope") -> SimpleNamespace:
    env = "SIM_MIRROR_" + path.upper().replace(".", "_")
    return SimpleNamespace(path=path, env=env, reach=reach, rule=SimpleNamespace(spec=lambda: spec))


SETTINGS = [
    _setting("stream.fps", {"kind": "whole", "low": 5, "high": 60}),
    _setting("stream.encoding", {"kind": "choice", "options": ["auto", "jpeg"]}),
    _setting(
        "device.developer_dir",
        {"kind": "text", "format": "path", "max_length": 1024, "required": False, "example": "/A"},
    ),
]


@pytest.mark.parametrize(
    ("changed", "said"),
    [
        (
            [
                _setting("stream.fps", {"kind": "whole", "low": 1, "high": 120}),
                _setting("stream.encoding", {"kind": "choice", "options": ["auto", "jpeg", "h264"]}),
                _setting(
                    "device.developer_dir",
                    {"kind": "text", "format": "path", "max_length": 2048, "required": False, "example": "/B"},
                ),
            ],
            [],
        ),
        (SETTINGS[1:], ["setting stream.fps: is gone"]),
        (
            [SimpleNamespace(**{**vars(SETTINGS[0]), "env": "SIM_FPS"}), *SETTINGS[1:]],
            ["setting stream.fps: is set by SIM_FPS, no longer SIM_MIRROR_STREAM_FPS"],
        ),
        (
            [_setting("stream.fps", {"kind": "whole", "low": 5, "high": 60}, reach="global"), *SETTINGS[1:]],
            ["setting stream.fps: can no longer be set for one scope"],
        ),
        ([_setting("stream.fps", {"kind": "flag"}), *SETTINGS[1:]], ["setting stream.fps: was a whole, now a flag"]),
        (
            [_setting("stream.fps", {"kind": "whole", "low": 10, "high": 30}), *SETTINGS[1:]],
            [
                "setting stream.fps: no longer accepts values below 10",
                "setting stream.fps: no longer accepts values above 30",
            ],
        ),
        (
            [SETTINGS[0], _setting("stream.encoding", {"kind": "choice", "options": ["auto"]}), SETTINGS[2]],
            ["setting stream.encoding: no longer accepts jpeg"],
        ),
        (
            [
                *SETTINGS[:2],
                _setting(
                    "device.developer_dir", {"kind": "text", "format": "name", "max_length": 64, "required": True}
                ),
            ],
            [
                'setting device.developer_dir: its format was "path", now "name"',
                "setting device.developer_dir: no longer accepts values above 64",
                "setting device.developer_dir: may no longer be empty",
            ],
        ),
    ],
)
def test_a_setting_keeps_its_name_its_variable_and_every_value_it_took(
    changed: list[SimpleNamespace], said: list[str]
) -> None:
    assert surface.setting_breaks(surface.describe_settings(SETTINGS), surface.describe_settings(changed)) == said


def _cli(
    *, extra: bool = False, required: bool = False, swapped: bool = False, drop: bool = False
) -> argparse.ArgumentParser:
    made = argparse.ArgumentParser(prog="sim-mirror")
    commands = made.add_subparsers()
    if not drop:
        token = commands.add_parser("token").add_subparsers().add_parser("create")
        token.add_argument("--kind", required=True)
        token.add_argument("--label")
        if extra:
            token.add_argument("--roots")
        if required:
            token.add_argument("--owner", required=True)
    serve = commands.add_parser("serve")
    for name in ("host", "port")[:: -1 if swapped else 1]:
        serve.add_argument(name, nargs="?")
    if required:
        serve.add_argument("mode")
    return made


def test_a_command_keeps_its_flags_and_arguments() -> None:
    promised = surface.describe_cli(_cli())
    assert promised["sim-mirror token create"] == {"options": {"--kind": True, "--label": False}, "positionals": []}
    assert promised["sim-mirror serve"] == {"options": {}, "positionals": [["host", False], ["port", False]]}
    assert surface.cli_breaks(promised, surface.describe_cli(_cli(extra=True))) == []
    assert surface.cli_breaks(promised, surface.describe_cli(_cli(required=True))) == [
        "cli sim-mirror serve: the new argument mode must be given",
        "cli sim-mirror token create --owner: must now be given",
    ]
    assert surface.cli_breaks(promised, surface.describe_cli(_cli(swapped=True))) == [
        "cli sim-mirror serve: its arguments were host port; now port host"
    ]
    assert surface.cli_breaks(promised, surface.describe_cli(_cli(drop=True))) == [
        "cli sim-mirror token: is gone",
        "cli sim-mirror token create: is gone",
    ]
    fewer = json.loads(json.dumps(promised))
    del fewer["sim-mirror token create"]["options"]["--label"]
    assert surface.cli_breaks(promised, fewer) == ["cli sim-mirror token create --label: is gone"]


# -- the viewer -------------------------------------------------------------------------------------------------------

VIEWER = {
    "index.ts": (
        "export { createViewer } from './viewer'\n"
        "export type { ViewerOptions, ViewHandle as Handle } from './viewer'\n"
        "export * from './protocol.generated'\n"
    ),
    "element.ts": (
        "export const ELEMENT_NAME = 'sim-mirror'\n"
        "  static readonly observedAttributes = ['server', 'scope']\n"
        "    this.#tell('close')\n"
    ),
    "viewer.ts": (
        "export interface ViewerOptions {\n  transport: T\n  /** Where. */\n  placement?: P\n  onClose?(): void\n}\n\n"
        "export interface ViewHandle {\n  readonly el: HTMLElement\n  connected(): boolean\n  encoding?: string\n}\n"
        'const STAGE = `<div part="stage"><canvas part="screen"></canvas></div>`\n'
    ),
    "viewer.test.ts": 'part="ignored"',
    # A property named by a template, `var(--sim-mirror-${name})`, leaves an empty name behind: not one of them.
    "styles.css": ":host { --sim-mirror-accent: blue; color: var(--sim-mirror-text); }\n.x { w: var(--sim-mirror-); }",
}


def _viewer(tmp_path: Path, **changes: str) -> Any:
    folder = tmp_path / "src"
    folder.mkdir(exist_ok=True)
    for name, text in (VIEWER | changes).items():
        (folder / name).write_text(text)
    return surface.describe_viewer(folder)


def test_the_viewer_is_read_from_its_source(tmp_path: Path) -> None:
    assert _viewer(tmp_path) == {
        "exports": ["Handle", "ViewerOptions", "createViewer"],
        "element": "sim-mirror",
        "attributes": ["server", "scope"],
        "events": ["close"],
        "parts": ["screen", "stage"],
        "custom_properties": ["--sim-mirror-accent", "--sim-mirror-text"],
        "interfaces": {
            "ViewerOptions": {"transport": False, "placement": True, "onClose": True},
            "HttpTransportOptions": {},
            "SimMirrorTransport": {},
            "ViewerState": {},
            "ViewHandle": {"el": False, "connected": False, "encoding": True},
        },
    }
    with pytest.raises(ValueError, match="ELEMENT_NAME"):
        _viewer(tmp_path, **{"element.ts": "nothing"})


def test_the_viewer_keeps_its_element_exports_and_what_a_page_gives_and_reads(tmp_path: Path) -> None:
    promised = _viewer(tmp_path)
    added = _viewer(
        tmp_path,
        **{
            "index.ts": VIEWER["index.ts"] + "export { more } from './more'\n",
            "viewer.ts": VIEWER["viewer.ts"]
            .replace("  onClose?(): void\n", "  onClose?(): void\n  icon?: I\n")
            .replace("  encoding?: string\n", "  encoding: string\n  focus(): void\n"),
        },
    )
    assert surface.viewer_breaks(promised, added) == []
    broken = _viewer(
        tmp_path,
        **{
            "index.ts": "export { createViewer } from './viewer'\n",
            "element.ts": VIEWER["element.ts"]
            .replace("'sim-mirror'", "'sim-view'")
            .replace(", 'scope'", "")
            .replace("close", "shut"),
            "viewer.ts": (
                "export interface ViewerOptions {\n  transport: T\n  placement: P\n  scope: string\n}\n"
                "export interface ViewHandle {\n  connected?(): boolean\n  encoding?: string\n}\n"
                '`<div part="stage"></div>`'
            ),
            "styles.css": ":host { --sim-mirror-accent: blue; }",
        },
    )
    assert surface.viewer_breaks(promised, broken) == [
        "viewer element: was <sim-mirror>, now <sim-view>",
        "viewer exports Handle: is gone",
        "viewer exports ViewerOptions: is gone",
        "viewer attributes scope: is gone",
        "viewer events close: is gone",
        "viewer parts screen: is gone",
        "viewer custom_properties --sim-mirror-text: is gone",
        "viewer ViewerOptions.placement: must now be given",
        "viewer ViewerOptions.onClose: is gone",
        "viewer ViewerOptions.scope: is new, and must be given",
        "viewer ViewHandle.el: is gone",
        "viewer ViewHandle.connected: may now be absent",
    ]
