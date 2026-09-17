# SPDX-License-Identifier: Apache-2.0
"""The generated reference pages: made from the code, checked for staleness, and refusing a source that is wrong."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import gen_docs
from gen_docs import SourceError, allowed, argument_type, compatibility_page, main, table
from sim_mirror.config import schema

REPO = Path(__file__).resolve().parents[2]
VALID_MATRIX = """
[[section]]
title = "Browsers"
column = "Browser"
note = "Where H.264 decodes."

[[section.row]]
name = "Chrome"
status = "verified 2026-09-20"
notes = "a | b"

[[section.row]]
name = "Lynx"
status = "unsupported"
"""


def repo_copy(root: Path, matrix: str = VALID_MATRIX) -> Path:
    shutil.copytree(REPO / "protocol" / "v1", root / "protocol" / "v1")
    (root / "compat").mkdir()
    (root / "compat" / "matrix.toml").write_text(matrix, encoding="utf-8")
    return root


def test_every_tool_is_listed_with_what_it_needs_and_its_arguments() -> None:
    text = gen_docs.tools_page()
    for name in ("sim_device", "sim_snapshot", "sim_screenshot", "sim_act", "sim_app", "sim_build_run", "sim_test"):
        assert f"## `{name}`" in text and f"(#{name})" in text
    assert "Needs a connector that can do `input_touch`." in text
    assert "can do `element_tree` -- or `screenshot`, reading the screen's pixels while `perception.ocr` is on." in text
    assert "| `steps` | array of object | 1 to 20 items | yes |" in text
    assert "| `mode` | string | `diff`, `full` | no |" in text
    assert "With build tools on:" in text and gen_docs.NOTICE in text


def test_argument_types_and_allowed_values_read_from_their_schema() -> None:
    assert argument_type({"oneOf": [{"type": "string"}, {"type": "object"}]}) == "string or object"
    assert argument_type({}) == "any" and argument_type({"type": "array"}) == "array of any"
    assert allowed({"type": "integer", "minimum": 1, "maximum": 6}) == "1 to 6"
    assert allowed({"maxItems": 3, "description": "ids"}) == "0 to 3 items; ids"
    assert allowed({"type": "boolean"}) == "—"


def test_every_setting_is_in_the_configuration_page() -> None:
    text = gen_docs.configuration_page()
    for setting in schema.SETTINGS:
        assert f"### `{setting.path}`" in text and f"`{setting.env}`" in text
    assert "## `[stream]`" in text and "## Top level" in text
    assert "- Default: `true` (a host embedding SimMirror: `false`)" in text
    assert "- Default: `[]`" in text and "- Default: empty" in text
    assert '[stream]\nencoding = "auto"' in text


def test_every_command_and_flag_is_in_the_cli_page() -> None:
    text = gen_docs.cli_page()
    for command in (
        "serve",
        "mcp",
        "open",
        "doctor",
        "config set",
        "devices choose",
        "token create",
        "tools",
        "version",
    ):
        assert f"## `sim-mirror {command}`" in text
    assert "| `--no-tap` |" in text and "| `--kind KIND` |" in text and "(one of: agent, viewer, admin, host)" in text
    assert "| `UDID` |" in text
    assert "## `sim-mirror serve`\n\nRun the SimMirror daemon on 127.0.0.1." in text


def test_the_protocol_page_lists_close_codes_and_constants() -> None:
    text = gen_docs.protocol_page(REPO)
    assert "| `4412` | `CLOSE_RESTARTING` |" in text and "| `TAG_H264` | `2` |" in text


def test_the_compatibility_page_is_made_from_its_matrix(tmp_path: Path) -> None:
    text = compatibility_page(repo_copy(tmp_path) / "compat" / "matrix.toml")
    assert "## Browsers\n\nWhere H.264 decodes." in text
    assert "| Chrome | **verified 2026-09-20** | a \\| b |" in text and "| Lynx | **unsupported** |  |" in text


@pytest.mark.parametrize(
    ("matrix", "said"),
    [
        ("not = [toml", "cannot read matrix.toml"),
        ("", "at least one [[section]]"),
        ('[[section]]\ncolumn = "C"\n', "a section: `title` is text"),
        ('[[section]]\ntitle = "T"\ncolumn = "C"\n', "T: has at least one row"),
        ('[[section]]\ntitle = "T"\ncolumn = "C"\n[[section.row]]\nstatus = "expected"\n', "T: `name` is text"),
        (
            '[[section]]\ntitle = "T"\ncolumn = "C"\n[[section.row]]\nname = "N"\nstatus = "works"\n',
            "T: N: status is expected, unsupported or verified YYYY-MM-DD",
        ),
        ('[[section]]\ntitle = "T"\n[[section.row]]\nname = "N"\nstatus = "expected"\n', "T: `column` is text"),
    ],
)
def test_a_matrix_that_is_wrong_says_how(tmp_path: Path, matrix: str, said: str) -> None:
    with pytest.raises(SourceError, match=said.replace("[", r"\[").replace("]", r"\]")):
        compatibility_page(repo_copy(tmp_path, matrix) / "compat" / "matrix.toml")


def test_main_writes_the_pages_then_finds_them_current_and_stale(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = repo_copy(tmp_path)
    assert main(["--check"], root=root) == 1
    assert "stale: docs/reference/tools.md" in capsys.readouterr().err
    assert main([], root=root) == 0
    assert "wrote docs/compatibility.md" in capsys.readouterr().out
    assert main(["--check"], root=root) == 0
    (root / "docs" / "reference" / "cli.md").write_text("edited by hand", encoding="utf-8")
    assert main(["--check"], root=root) == 1
    assert capsys.readouterr().err.strip() == "stale: docs/reference/cli.md -- run `make generate`"


def test_main_refuses_a_source_it_cannot_read(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = repo_copy(tmp_path, "")
    assert main([], root=root) == 2
    assert "docs: matrix.toml: lists at least one [[section]]" in capsys.readouterr().err
    (root / "protocol" / "v1" / "constants.json").write_text("{", encoding="utf-8")
    assert main([], root=root) == 2
    assert "cannot read constants.json" in capsys.readouterr().err


def test_this_repository_s_pages_are_current() -> None:
    assert main(["--check"]) == 0


def test_a_table_cell_keeps_its_pipes_and_lines_inside_it() -> None:
    assert table(("A",), [("x|y\nz",)]) == "| A |\n|---|\n| x\\|y z |"
