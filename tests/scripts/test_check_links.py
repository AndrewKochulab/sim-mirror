# SPDX-License-Identifier: Apache-2.0
"""The link check: relative links and anchors in Markdown must be there; web links and code are left alone."""

from __future__ import annotations

from pathlib import Path

import pytest

import check_links
from check_links import heading_slugs, links_in, main, problems, slug


def write(root: Path, files: dict[str, str]) -> list[str]:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return sorted(files)


GOOD = {
    "README.md": (
        "# Top heading\n\n"
        "[a](docs/a.md) [b](docs/a.md#section-two) [own](#top-heading) [dir](docs/)\n"
        '![pic](docs/media/x.png) [root](/docs/a.md "title") [web](https://example.com/x) [mail](mailto:a@b.c)\n'
        "`[not a link](nowhere.md)`\n\n"
        "```md\n[also not](nowhere.md)\n```\n"
    ),
    "docs/a.md": "# A\n\n## Section two\n\n[back](../README.md#top-heading)\n",
    "docs/media/x.png": "png",
    "notes.txt": "[ignored](nowhere.md)",
}


def test_links_that_are_there_pass(tmp_path: Path) -> None:
    assert problems(tmp_path, write(tmp_path, GOOD)) == []


@pytest.mark.parametrize(
    ("line", "said"),
    [
        ("[x](docs/missing.md)", "README.md:1: docs/missing.md is not there"),
        ("[x](docs/a.md#no-such)", "README.md:1: docs/a.md#no-such names no heading in docs/a.md"),
        ("[x](#nowhere)", "README.md:1: #nowhere names no heading in README.md"),
        ("[x](../outside.md)", "README.md:1: ../outside.md leaves the repository"),
    ],
)
def test_each_broken_link_is_named(tmp_path: Path, line: str, said: str) -> None:
    # The broken link stays on line 1; the heading docs/a.md links back to stays too.
    files = {**GOOD, "README.md": line + "\n\n# Top heading\n"}
    assert problems(tmp_path, write(tmp_path, files)) == [said]


def test_headings_become_anchors_as_on_github() -> None:
    assert slug("Hello, World!") == "hello-world"
    assert slug("`sim_device`") == "sim_device"
    assert slug("Xcode 27 & Device Hub") == "xcode-27--device-hub"
    assert slug("Open **Settings → General**") == "open-settings--general"
    assert slug("[Link text](https://example.com)") == "link-text"
    text = "# Setup\n\n## Setup\n\n```\n# not a heading\n```\n### Done ##\n"
    assert heading_slugs(text) == {"setup", "setup-1", "done"}


def test_links_are_read_outside_code() -> None:
    text = "[a](x.md) `[b](y.md)`\n```\n[c](z.md)\n```\n![d](p.png) [e [nested]](n.md)\n"
    assert links_in(text) == [(1, "x.md"), (5, "p.png"), (5, "n.md")]


def test_this_repository_passes(capsys: pytest.CaptureFixture[str]) -> None:
    assert main() == 0
    assert "links ok" in capsys.readouterr().out


def test_a_broken_repository_lists_its_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    files = write(tmp_path, {"README.md": "[x](gone.md)\n"})
    monkeypatch.setattr(check_links, "repo_files", lambda root: files)
    assert main(tmp_path) == 1
    assert "README.md:1: gone.md is not there" in capsys.readouterr().err
