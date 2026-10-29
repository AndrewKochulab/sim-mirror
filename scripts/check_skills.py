# SPDX-License-Identifier: Apache-2.0
"""Validate short, self-contained skills and optionally package the same files for other clients."""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from collections.abc import Sequence
from pathlib import Path

from _repo import REPO_ROOT
from check_distribution import frontmatter
from check_links import EXTERNAL, links_in
from sim_mirror.tools.schemas import SCHEMAS

SKILLS = "plugins/sim-mirror/skills"
NAMES = frozenset({"simulator", "sim-mirror-setup", "sim-mirror-build-test", "sim-mirror-capture"})
WORD_LIMIT = 500
TOOL = re.compile(r"\bsim_[a-z][a-z_]+\b")


def problems(root: Path) -> list[str]:
    found: list[str] = []
    base = root / SKILLS
    entries = {path.parent.name: path for path in base.glob("*/SKILL.md")}
    if entries.keys() != NAMES:
        found.append("skills: expected " + ", ".join(sorted(NAMES)))
    for name, entry in sorted(entries.items()):
        folder = entry.parent.resolve()
        reached = {entry.resolve()}
        for path in sorted(folder.rglob("*.md")):
            content = path.read_text(encoding="utf-8")
            rel = path.relative_to(folder)
            if len(content.split()) > WORD_LIMIT:
                found.append(f"{name}/{rel}: exceeds {WORD_LIMIT} words; split conditional guidance")
            if path == entry.resolve():
                fields = frontmatter(content)
                if fields.get("name") != name or not fields.get("description"):
                    found.append(f"{name}: needs matching name and description")
            for tool in sorted(set(TOOL.findall(content)) - SCHEMAS.keys()):
                found.append(f"{name}/{rel}: unknown tool {tool}")
            for _, target in links_in(content):
                if EXTERNAL.match(target):
                    continue
                destination = (path.parent / target.split("#", 1)[0]).resolve()
                if not destination.is_file() or not destination.is_relative_to(folder) or destination.suffix != ".md":
                    found.append(f"{name}/{rel}: reference {target} is not bundled in this skill")
                if path == entry.resolve():
                    reached.add(destination)
        for path in sorted(folder.rglob("*.md")):
            if path.resolve() not in reached:
                found.append(f"{name}/{path.relative_to(folder)}: link directly from SKILL.md")
    return found


def package(root: Path, output: Path) -> None:
    """A reproducible archive of the canonical skills, with no repository-only reference dependencies."""
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted((root / SKILLS).rglob("*.md")):
            info = zipfile.ZipInfo(path.relative_to(root / SKILLS).as_posix())
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())


def main(argv: Sequence[str] | None = None, *, root: Path = REPO_ROOT) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="also write the portable skills ZIP")
    args = parser.parse_args(argv)
    errors = problems(root)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    if args.output is not None:
        package(root, args.output)
    print("skills ok: short entrypoints, known tools and bundled references")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
