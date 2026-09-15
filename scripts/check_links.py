# SPDX-License-Identifier: Apache-2.0
"""Fail when a Markdown file links to a file or a heading that is not there.

Relative links and images are resolved against the linking file (a link starting with ``/`` against the repository
root), and a ``#heading`` is looked up among the target's headings as GitHub makes their anchors. Web and mail links are
not fetched: this runs offline, on every commit. Code blocks and inline code are not links.

Run directly, or via `make lint`.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable
from pathlib import Path

from _repo import REPO_ROOT, read_text, repo_files

LINK = re.compile(r"!?\[(?:[^\]\[]|\[[^\]]*\])*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
INLINE_CODE = re.compile(r"`[^`]*`")
EXTERNAL = re.compile(r"\A[a-zA-Z][a-zA-Z0-9+.-]*:")


def prose_lines(text: str) -> Iterable[tuple[int, str]]:
    """Each line outside a fenced code block, numbered from 1."""
    fenced = False
    for lineno, line in enumerate(text.splitlines(), start=1):
        if FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            yield lineno, line


def slug(heading: str) -> str:
    """A heading's anchor as GitHub makes it: its text, lower-cased, without punctuation, spaces as hyphens."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading)
    text = text.replace("`", "").replace("*", "")
    return re.sub(r"[^\w\- ]", "", text.lower()).replace(" ", "-")


def heading_slugs(text: str) -> set[str]:
    """Every anchor the file's headings make, numbering repeats as GitHub does (``setup``, ``setup-1``)."""
    seen: dict[str, int] = {}
    found: set[str] = set()
    for _, line in prose_lines(text):
        match = HEADING.match(line)
        if not match:
            continue
        base = slug(match.group(2))
        count = seen.get(base, 0)
        seen[base] = count + 1
        found.add(base if count == 0 else f"{base}-{count}")
    return found


def links_in(text: str) -> list[tuple[int, str]]:
    return [
        (lineno, match.group(1))
        for lineno, line in prose_lines(text)
        for match in LINK.finditer(INLINE_CODE.sub("", line))
    ]


def problems(root: Path, files: Iterable[str]) -> list[str]:
    found: list[str] = []
    root = root.resolve()
    for rel in files:
        if not rel.endswith(".md"):
            continue
        text = read_text(root / rel) or ""
        for lineno, target in links_in(text):
            if EXTERNAL.match(target):
                continue
            path_part, _, anchor = target.partition("#")
            base = root if path_part.startswith("/") else (root / rel).parent
            resolved = (base / path_part.lstrip("/")).resolve() if path_part else (root / rel).resolve()
            where = f"{rel}:{lineno}: {target}"
            if resolved != root and root not in resolved.parents:
                found.append(f"{where} leaves the repository")
            elif not resolved.exists():
                found.append(f"{where} is not there")
            elif anchor and resolved.suffix == ".md" and anchor not in heading_slugs(read_text(resolved) or ""):
                found.append(f"{where} names no heading in {resolved.relative_to(root).as_posix()}")
    return found


def main(root: Path = REPO_ROOT) -> int:
    found = problems(root, repo_files(root))
    if not found:
        print("links ok: every relative link and anchor in the Markdown files is there")
        return 0
    print("broken links:\n", file=sys.stderr)
    for problem in found:
        print(f"  {problem}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
