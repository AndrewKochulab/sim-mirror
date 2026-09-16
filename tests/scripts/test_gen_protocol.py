# SPDX-License-Identifier: Apache-2.0
"""The protocol generator: the committed files are current, the schema subset renders both ways, and anything outside
that subset is refused rather than guessed."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

import gen_protocol


def test_the_committed_generated_files_are_current() -> None:
    assert gen_protocol.main(["--check"]) == 0


def _folder(
    tmp_path: Path,
    schemas: dict[str, dict[str, Any]],
    constants: dict[str, Any] | None = None,
    codes: dict[str, Any] | None = None,
) -> Path:
    folder = tmp_path / "v1"
    folder.mkdir(parents=True, exist_ok=True)
    for name, schema in schemas.items():
        (folder / name).write_text(json.dumps(schema))
    (folder / "constants.json").write_text(
        json.dumps(constants or {"$comment": "x", "protocol_version": 1, "tag_a": 1})
    )
    (folder / "close-codes.json").write_text(json.dumps(codes or {"close_codes": {"gone": {"code": 4410}}}))
    return folder


SAMPLE = {
    "b.schema.json": {
        "title": "Event",
        "oneOf": [{"$ref": "#/$defs/Moved"}, {"$ref": "a.schema.json#/$defs/Base"}],
        "$defs": {
            "Pair": {
                "description": "Two shares.",
                "type": "array",
                "prefixItems": [{"$ref": "a.schema.json#/$defs/Share"}, {"$ref": "a.schema.json#/$defs/Share"}],
            },
            "Moved": {
                "allOf": [
                    {"$ref": "a.schema.json#/$defs/Base"},
                    {"type": "object", "properties": {"to": {"$ref": "#/$defs/Pair"}}, "required": ["to"]},
                ]
            },
        },
    },
    "a.schema.json": {
        "$defs": {
            "Share": {"type": "number"},
            "Colour": {"description": "A colour.", "x-constant": "COLOURS", "enum": ["red", "blue"]},
            "Base": {
                "description": "Something with a kind.",
                "type": "object",
                "properties": {
                    "kind": {"const": "base", "description": "Which one it is."},
                    "v": {"const": 1},
                    "note": {"type": ["string", "null"]},
                    "colour": {"$ref": "#/$defs/Colour"},
                    "mode": {"enum": ["on", "off"]},
                    "tags": {"type": "array", "items": {"oneOf": [{"type": "string"}, {"type": "integer"}]}},
                    "counts": {"type": "array", "items": {"type": "boolean"}},
                    "size": {"type": "object", "properties": {"w": {"type": "integer"}}, "required": ["w"]},
                },
                "required": ["kind", "v", "note", "colour", "mode", "tags", "counts", "size"],
            },
            "Empty": {"type": "object"},
            "Open": {
                "type": "object",
                "properties": {"input": {"type": "object", "additionalProperties": True}},
                "required": ["input"],
            },
        }
    },
}


def test_a_folder_renders_to_python_that_imports_and_typescript_that_says_the_same(tmp_path: Path) -> None:
    folder = _folder(tmp_path, SAMPLE)
    python_out, typescript_out = tmp_path / "out" / "gen.py", tmp_path / "out" / "gen.ts"
    assert gen_protocol.main([], schema_dir=folder, python_out=python_out, typescript_out=typescript_out) == 0
    python = python_out.read_text()
    assert "PROTOCOL_VERSION: Final = 1" in python and "CLOSE_GONE: Final = 4410" in python
    assert 'COLOURS: tuple[Colour, ...] = (\n    "red",\n    "blue",\n)' in python
    assert "class Moved(Base):" in python and "    to: Pair" in python
    assert '    #: Which one it is.\n    kind: Literal["base"]' in python
    assert "    note: str | None" in python and '    mode: Literal["on", "off"]' in python
    assert "    tags: list[str | int]" in python and "    size: BaseSize" in python
    assert "class Empty(TypedDict):\n    pass" in python
    assert "class Open(TypedDict):\n    input: dict[str, Any]" in python and "class OpenInput" not in python
    assert python.index("Share = float") < python.index("Pair = tuple[Share, Share]")
    assert python.index("class Base(TypedDict)") < python.index("class Moved(Base)") < python.index("Event = Moved")
    spec = importlib.util.spec_from_file_location("generated_sample", python_out)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.COLOURS == ("red", "blue") and module.TAG_A == 1

    typescript = typescript_out.read_text()
    assert "export const COLOURS = ['red', 'blue'] as const" in typescript
    assert "export type Colour = (typeof COLOURS)[number]" in typescript
    assert "export interface Moved extends Base {\n  to: Pair\n}" in typescript
    assert "  // Which one it is.\n  kind: 'base'" in typescript and "  v: 1" in typescript
    assert "  note: string | null" in typescript and "  tags: (string | number)[]" in typescript
    assert "  counts: boolean[]" in typescript and "export type Pair = [Share, Share]" in typescript
    assert "export interface Open {\n  input: Record<string, unknown>\n}" in typescript
    assert "export type Event = Moved | Base" in typescript and "export const CLOSE_GONE = 4410" in typescript


@pytest.mark.parametrize(
    ("schemas", "says"),
    [
        (
            {"a.schema.json": {"$defs": {"X": {"type": "object", "properties": {"y": {"type": "string"}}}}}},
            "every property is required",
        ),
        ({"a.schema.json": {"$defs": {"X": {"enum": ["a"]}}}}, "x-constant"),
        ({"a.schema.json": {"$defs": {"X": {"enum": [1], "x-constant": "XS"}}}}, "x-constant"),
        ({"a.schema.json": {"$defs": {"X": {"allOf": [{"type": "object"}]}}}}, "allOf is one $ref"),
        ({"a.schema.json": {"$defs": {"X": {"oneOf": [{"type": "string"}]}}}}, "only $refs"),
        ({"a.schema.json": {"$defs": {"X": {"type": "array"}}}}, "no type can be generated"),
        ({"a.schema.json": {"$defs": {"X": {"$ref": "#/$defs/Missing"}}}}, "references to nothing defined: Missing"),
        ({"a.schema.json": {"$defs": {"X": {"$ref": "other.schema.json#/$defs/Y"}}}}, "cannot follow the reference"),
        ({"a.schema.json": {"$defs": {"X": {"$ref": "#/properties/y"}}}}, "cannot follow the reference"),
        (
            {
                "a.schema.json": {"$defs": {"X": {"type": "string"}}},
                "b.schema.json": {"$defs": {"X": {"type": "string"}}},
            },
            "X is defined twice",
        ),
        (
            {
                "a.schema.json": {
                    "$defs": {
                        "C": {"x-constant": "CS", "enum": ["a"]},
                        "X": {"allOf": [{"$ref": "#/$defs/C"}, {"type": "object"}]},
                    }
                }
            },
            "X extends C, which is not an object",
        ),
    ],
)
def test_what_is_outside_the_subset_is_refused(
    tmp_path: Path, schemas: dict[str, dict[str, Any]], says: str, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = _folder(tmp_path, schemas)
    assert gen_protocol.main([], schema_dir=folder, python_out=tmp_path / "g.py", typescript_out=tmp_path / "g.ts") == 2
    assert says in capsys.readouterr().err and not (tmp_path / "g.py").exists()


@pytest.mark.parametrize(
    ("constants", "codes", "says"),
    [
        ({"protocol_version": "1"}, None, "constants are whole numbers: PROTOCOL_VERSION"),
        ({"tag": True, "protocol_version": 1}, None, "TAG"),
        ({"tag": 1}, None, "names the protocol_version"),
        (None, {"close_codes": {"x": {}}}, "CLOSE_X"),
    ],
)
def test_constants_are_whole_numbers_and_name_the_version(
    tmp_path: Path,
    constants: dict[str, Any] | None,
    codes: dict[str, Any] | None,
    says: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    folder = _folder(tmp_path, {}, constants, codes)
    assert gen_protocol.main([], schema_dir=folder, python_out=tmp_path / "g.py", typescript_out=tmp_path / "g.ts") == 2
    assert says in capsys.readouterr().err


def test_an_unreadable_file_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    folder = _folder(tmp_path, {})
    (folder / "broken.schema.json").write_text("{")
    assert gen_protocol.main([], schema_dir=folder, python_out=tmp_path / "g.py", typescript_out=tmp_path / "g.ts") == 2
    assert "cannot read broken.schema.json" in capsys.readouterr().err


def test_check_names_stale_files_and_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    folder = _folder(tmp_path, SAMPLE)
    outputs = {"python_out": tmp_path / "g.py", "typescript_out": tmp_path / "g.ts"}
    assert gen_protocol.main(["--check"], schema_dir=folder, **outputs) == 1
    assert "stale:" in capsys.readouterr().err and not outputs["python_out"].exists()
    assert gen_protocol.main([], schema_dir=folder, **outputs) == 0
    assert gen_protocol.main(["--check"], schema_dir=folder, **outputs) == 0
    outputs["typescript_out"].write_text("edited")
    assert gen_protocol.main(["--check"], schema_dir=folder, **outputs) == 1
