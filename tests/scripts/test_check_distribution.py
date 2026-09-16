# SPDX-License-Identifier: Apache-2.0
"""The distribution check: what installs SimMirror names the package's version and has what its reader needs."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import check_distribution
from check_distribution import frontmatter, main, problems

VERSION = "1.2.3"
PIN = f"sim-mirror=={VERSION}"


def valid_files() -> dict[str, Any]:
    return {
        "src/sim_mirror/_version.py": f'"""The version."""\n\n__version__ = "{VERSION}"\n',
        "viewer/package.json": {"name": "@andrewkochulab/sim-mirror", "version": VERSION},
        ".claude-plugin/marketplace.json": {
            "name": "sim-mirror",
            "owner": {"name": "Andrew Kochulab"},
            "plugins": [{"name": "sim-mirror", "source": "./plugins/sim-mirror", "version": VERSION}],
        },
        "plugins/sim-mirror/.claude-plugin/plugin.json": {
            "name": "sim-mirror",
            "version": VERSION,
            "description": "The simulator.",
            "license": "Apache-2.0",
        },
        "plugins/sim-mirror/.mcp.json": {
            "mcpServers": {"sim-mirror": {"command": "uvx", "args": ["--from", PIN, "sim-mirror", "mcp"]}}
        },
        "plugins/sim-mirror/skills/simulator/SKILL.md": "---\nname: simulator\ndescription: Drive it.\n---\n\nHow.\n",
        "plugins/sim-mirror/commands/open.md": "---\ndescription: Open the viewer\n---\n\nRun it.\n",
        "server.json": {
            "$schema": check_distribution.REGISTRY_SCHEMA,
            "name": "io.github.AndrewKochulab/sim-mirror",
            "description": "Mirror and drive the simulator.",
            "version": VERSION,
            "packages": [{"identifier": "sim-mirror", "version": VERSION, "transport": {"type": "stdio"}}],
        },
        "README.md": f"uvx --from {PIN} sim-mirror doctor\nnpm install @andrewkochulab/sim-mirror@{VERSION}\n",
        "CHANGELOG.md": "Installed with sim-mirror@v0.0.1 once.\n",
    }


def write(root: Path, files: dict[str, Any]) -> list[str]:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
    return sorted(files)


def test_a_repository_whose_files_agree_passes(tmp_path: Path) -> None:
    assert problems(tmp_path, write(tmp_path, valid_files())) == []


def test_this_repository_passes(capsys: pytest.CaptureFixture[str]) -> None:
    assert main() == 0
    assert "distribution ok" in capsys.readouterr().out


def _set(rel: str, *path: str, value: Any) -> Callable[[dict[str, Any]], None]:
    def change(files: dict[str, Any]) -> None:
        target = files[rel]
        for key in path[:-1]:
            target = target[key] if isinstance(target, dict) else target[int(key)]
        if isinstance(target, list):
            target[int(path[-1])] = value
        else:
            target[path[-1]] = value

    return change


def _replace(rel: str, content: Any) -> Callable[[dict[str, Any]], None]:
    def change(files: dict[str, Any]) -> None:
        files[rel] = content

    return change


def _remove(rel: str) -> Callable[[dict[str, Any]], None]:
    def change(files: dict[str, Any]) -> None:
        del files[rel]

    return change


MARKET = ".claude-plugin/marketplace.json"
PLUGIN_JSON = "plugins/sim-mirror/.claude-plugin/plugin.json"
MCP = "plugins/sim-mirror/.mcp.json"
SKILL = "plugins/sim-mirror/skills/simulator/SKILL.md"
COMMAND = "plugins/sim-mirror/commands/open.md"


@pytest.mark.parametrize(
    ("change", "said"),
    [
        (_set(MARKET, "name", value="other"), "the marketplace is named 'sim-mirror'"),
        (_set(MARKET, "owner", value={}), "owner.name is required"),
        (_set(MARKET, "plugins", value=[]), "lists the sim-mirror plugin exactly once"),
        (_set(MARKET, "plugins", "0", "source", value="./elsewhere"), "source is ./plugins/sim-mirror"),
        (_set(MARKET, "plugins", "0", "version", value="1.0.0"), "plugin's version is 1.2.3, not '1.0.0'"),
        (_replace(MARKET, "{not json"), "marketplace.json is not JSON"),
        (_replace(MARKET, []), "marketplace.json is not a JSON object"),
        (_remove(MARKET), "marketplace.json is missing"),
        (_set(PLUGIN_JSON, "name", value="other"), "the plugin is named 'sim-mirror'"),
        (_set(PLUGIN_JSON, "version", value="1.0.0"), "plugin.json: the version is 1.2.3"),
        (_set(PLUGIN_JSON, "description", value=""), "a description is required"),
        (_set(PLUGIN_JSON, "license", value="MIT"), "the license is Apache-2.0"),
        (_set(MCP, "mcpServers", "sim-mirror", "command", value="pipx"), "runs `uvx … sim-mirror mcp`"),
        (_set(MCP, "mcpServers", "sim-mirror", "args", value="mcp"), "runs `uvx … sim-mirror mcp`"),
        (_remove(SKILL), "the plugin has a skill"),
        (_replace(SKILL, "---\nname: other\ndescription: x\n---\n"), "names the skill 'simulator'"),
        (_replace(SKILL, "No frontmatter.\n"), "names the skill 'simulator'"),
        (_replace(COMMAND, "---\nargument-hint: none\n---\n"), "describes the command"),
        (_set("server.json", "$schema", value="https://example.com/schema.json"), "$schema is"),
        (_set("server.json", "name", value="io.github.other/sim-mirror"), "the name is io.github.AndrewKochulab"),
        (_set("server.json", "version", value="1.0.0"), "server.json: the version is 1.2.3"),
        (_set("server.json", "description", value="x" * 101), "the description is 1 to 100 characters"),
        (_set("server.json", "packages", value=[]), "lists the package"),
        (_set("server.json", "packages", "0", "version", value="1.0.0"), "each package is sim-mirror 1.2.3"),
        (_set("server.json", "packages", "0", "transport", value={"type": "sse"}), "transport is stdio"),
        (_replace("server.json", "[]"), "server.json is not a JSON object"),
        (_set("viewer/package.json", "version", value="1.0.0"), "the viewer's version is 1.2.3, not '1.0.0'"),
        (
            _replace("README.md", "uvx --from git+https://github.com/AndrewKochulab/sim-mirror@v1.0.0 x\n"),
            "README.md:1: pins 1.0.0, not 1.2.3",
        ),
        (
            _replace("docs.md", "https://github.com/AndrewKochulab/sim-mirror/releases/download/v1.2.4-rc.1/x.tgz\n"),
            "docs.md:1: pins 1.2.4-rc.1, not 1.2.3",
        ),
        (_replace("docs.md", "uv tool install sim-mirror==1.2.2\n"), "docs.md:1: pins 1.2.2, not 1.2.3"),
        (_replace("docs.md", "npm install @andrewkochulab/sim-mirror@1.3.0\n"), "docs.md:1: pins 1.3.0, not 1.2.3"),
    ],
)
def test_each_disagreement_is_named(tmp_path: Path, change: Callable[[dict[str, Any]], None], said: str) -> None:
    files = valid_files()
    change(files)
    found = problems(tmp_path, write(tmp_path, files))
    assert any(said in problem for problem in found), found


def test_a_package_without_a_version_is_named_first(tmp_path: Path) -> None:
    files = valid_files()
    files["src/sim_mirror/_version.py"] = '"""No version here."""\n'
    assert problems(tmp_path, write(tmp_path, files)) == ["src/sim_mirror/_version.py sets no __version__"]
    assert problems(tmp_path / "empty", []) == ["src/sim_mirror/_version.py sets no __version__"]


def test_frontmatter_is_read_between_its_fences() -> None:
    assert frontmatter("---\nname: a\ndescription: b: c\nnot a field\n---\nbody") == {
        "name": "a",
        "description": "b: c",
    }
    assert frontmatter("name: a\n") == {}


def test_a_failing_repository_lists_every_problem(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    files = valid_files()
    files["server.json"]["version"] = "0.0.9"
    write(tmp_path, files)
    subprocess_free = check_distribution.repo_files
    try:
        check_distribution.repo_files = lambda root: sorted(files)  # type: ignore[assignment]
        assert main(tmp_path) == 1
    finally:
        check_distribution.repo_files = subprocess_free  # type: ignore[assignment]
    assert "server.json: the version is 1.2.3" in capsys.readouterr().err
