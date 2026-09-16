# SPDX-License-Identifier: Apache-2.0
"""Fail when what installs SimMirror disagrees with the package itself.

The Claude Code plugin and its marketplace entry, the MCP registry entry, the viewer's npm package and every install
command pinned to a release tag must name the package's version, and each file must have the fields its reader needs.
A release that bumps `_version.py` and forgets one of them would hand people the previous release.

Run directly, or via `make lint`.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from _repo import REPO_ROOT, read_text, repo_files

NAME = "sim-mirror"
LICENSE = "Apache-2.0"
REGISTRY_NAME = "io.github.AndrewKochulab/sim-mirror"
REGISTRY_SCHEMA = "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"
REGISTRY_DESCRIPTION_MAX = 100
VERSION_FILE = "src/sim_mirror/_version.py"
VIEWER_PACKAGE = "viewer/package.json"
MARKETPLACE = ".claude-plugin/marketplace.json"
PLUGIN = "plugins/sim-mirror"
REGISTRY = "server.json"
#: An install pinned to a release: ``sim-mirror==0.2.0`` from PyPI, ``@andrewkochulab/sim-mirror@0.2.0`` from npm, or a
#: release tag -- ``…/sim-mirror@v0.1.0``, ``…/sim-mirror/releases/download/v0.1.0/…``.
PINNED = re.compile(
    r"(?:sim-mirror==|@andrewkochulab/sim-mirror@|sim-mirror(?:@|/releases/download/)v)"
    r"(\d+\.\d+\.\d+(?:[-.+][0-9A-Za-z.]+)?)"
)
#: Files that may name other versions: this check and its test, history, and lock files.
PIN_EXEMPT = frozenset(
    {
        "scripts/check_distribution.py",
        "tests/scripts/test_check_distribution.py",
        "CHANGELOG.md",
        "uv.lock",
        "viewer/package-lock.json",
    }
)
_VERSION = re.compile(r"^__version__ = \"([^\"]+)\"$", re.MULTILINE)
_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


def package_version(root: Path) -> str | None:
    match = _VERSION.search(read_text(root / VERSION_FILE) or "") if (root / VERSION_FILE).is_file() else None
    return match.group(1) if match else None


def load_json(root: Path, rel: str, problems: list[str]) -> dict[str, Any]:
    """The file's object, or an empty one with a problem saying why there is none."""
    try:
        loaded = json.loads((root / rel).read_text(encoding="utf-8"))
    except FileNotFoundError:
        problems.append(f"{rel} is missing")
        return {}
    except ValueError as exc:
        problems.append(f"{rel} is not JSON: {exc}")
        return {}
    if not isinstance(loaded, dict):
        problems.append(f"{rel} is not a JSON object")
        return {}
    return loaded


def frontmatter(text: str) -> dict[str, str]:
    """The ``key: value`` lines between a Markdown file's opening ``---`` fences."""
    match = _FRONTMATTER.match(text)
    if not match:
        return {}
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip():
            fields[key.strip()] = value.strip()
    return fields


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def marketplace_problems(root: Path, version: str) -> list[str]:
    problems: list[str] = []
    market = load_json(root, MARKETPLACE, problems)
    if not market:
        return problems
    if market.get("name") != NAME:
        problems.append(f"{MARKETPLACE}: the marketplace is named {NAME!r}, so plugins install as {NAME}@{NAME}")
    if not _object(market.get("owner")).get("name"):
        problems.append(f"{MARKETPLACE}: owner.name is required")
    entries = [_object(entry) for entry in market.get("plugins") or [] if _object(entry).get("name") == NAME]
    if len(entries) != 1:
        problems.append(f"{MARKETPLACE}: lists the {NAME} plugin exactly once")
        return problems
    entry = entries[0]
    if entry.get("source") != f"./{PLUGIN}":
        problems.append(f"{MARKETPLACE}: the {NAME} plugin's source is ./{PLUGIN}")
    if entry.get("version") != version:
        problems.append(f"{MARKETPLACE}: the {NAME} plugin's version is {version}, not {entry.get('version')!r}")
    return problems


def plugin_problems(root: Path, version: str) -> list[str]:
    problems: list[str] = []
    manifest = load_json(root, f"{PLUGIN}/.claude-plugin/plugin.json", problems)
    if manifest:
        if manifest.get("name") != NAME:
            problems.append(f"{PLUGIN}/.claude-plugin/plugin.json: the plugin is named {NAME!r}")
        if manifest.get("version") != version:
            problems.append(f"{PLUGIN}/.claude-plugin/plugin.json: the version is {version}")
        if not manifest.get("description"):
            problems.append(f"{PLUGIN}/.claude-plugin/plugin.json: a description is required")
        if manifest.get("license") != LICENSE:
            problems.append(f"{PLUGIN}/.claude-plugin/plugin.json: the license is {LICENSE}")
    servers = _object(load_json(root, f"{PLUGIN}/.mcp.json", problems).get("mcpServers"))
    server = _object(servers.get(NAME))
    raw_args = server.get("args")
    args = raw_args if isinstance(raw_args, list) else []
    if (root / PLUGIN / ".mcp.json").is_file() and (server.get("command") != "uvx" or args[-2:] != [NAME, "mcp"]):
        problems.append(f"{PLUGIN}/.mcp.json: the {NAME} server runs `uvx … {NAME} mcp`")
    skills = sorted((root / PLUGIN / "skills").glob("*/SKILL.md"))
    if not skills:
        problems.append(f"{PLUGIN}/skills: the plugin has a skill")
    for skill in skills:
        fields = frontmatter(skill.read_text(encoding="utf-8"))
        rel = skill.relative_to(root).as_posix()
        if fields.get("name") != skill.parent.name or not fields.get("description"):
            problems.append(f"{rel}: its frontmatter names the skill {skill.parent.name!r} and describes it")
    for command in sorted((root / PLUGIN / "commands").glob("*.md")):
        if not frontmatter(command.read_text(encoding="utf-8")).get("description"):
            problems.append(f"{command.relative_to(root).as_posix()}: its frontmatter describes the command")
    return problems


def registry_problems(root: Path, version: str) -> list[str]:
    problems: list[str] = []
    entry = load_json(root, REGISTRY, problems)
    if not entry:
        return problems
    if entry.get("$schema") != REGISTRY_SCHEMA:
        problems.append(f"{REGISTRY}: $schema is {REGISTRY_SCHEMA}")
    if entry.get("name") != REGISTRY_NAME:
        problems.append(f"{REGISTRY}: the name is {REGISTRY_NAME}")
    if entry.get("version") != version:
        problems.append(f"{REGISTRY}: the version is {version}")
    description = entry.get("description")
    if not isinstance(description, str) or not 0 < len(description) <= REGISTRY_DESCRIPTION_MAX:
        problems.append(f"{REGISTRY}: the description is 1 to {REGISTRY_DESCRIPTION_MAX} characters")
    packages = [_object(package) for package in entry.get("packages") or []]
    if not packages:
        problems.append(f"{REGISTRY}: lists the package")
    for package in packages:
        if package.get("identifier") != NAME or package.get("version") != version:
            problems.append(f"{REGISTRY}: each package is {NAME} {version}")
        if _object(package.get("transport")).get("type") != "stdio":
            problems.append(f"{REGISTRY}: each package's transport is stdio")
    return problems


def viewer_problems(root: Path, version: str) -> list[str]:
    problems: list[str] = []
    package = load_json(root, VIEWER_PACKAGE, problems)
    if package and package.get("version") != version:
        problems.append(f"{VIEWER_PACKAGE}: the viewer's version is {version}, not {package.get('version')!r}")
    return problems


def pin_problems(root: Path, files: Iterable[str], version: str) -> list[str]:
    problems: list[str] = []
    for rel in files:
        if rel in PIN_EXEMPT:
            continue
        text = read_text(root / rel)
        for lineno, line in enumerate((text or "").splitlines(), start=1):
            for match in PINNED.finditer(line):
                if match.group(1) != version:
                    problems.append(f"{rel}:{lineno}: pins {match.group(1)}, not {version}")
    return problems


def problems(root: Path, files: Iterable[str]) -> list[str]:
    version = package_version(root)
    if version is None:
        return [f"{VERSION_FILE} sets no __version__"]
    return [
        *marketplace_problems(root, version),
        *plugin_problems(root, version),
        *registry_problems(root, version),
        *viewer_problems(root, version),
        *pin_problems(root, files, version),
    ]


def main(root: Path = REPO_ROOT) -> int:
    found = problems(root, repo_files(root))
    if not found:
        print("distribution ok: the plugin, marketplace, registry entry, viewer and pinned installs agree")
        return 0
    print("what installs SimMirror disagrees with the package:\n", file=sys.stderr)
    for problem in found:
        print(f"  {problem}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
