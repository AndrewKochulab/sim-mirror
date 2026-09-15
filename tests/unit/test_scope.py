# SPDX-License-Identifier: Apache-2.0
"""Scopes: ids that are safe in a URL and a file name, and one per project folder."""

from __future__ import annotations

from pathlib import Path

import pytest

from sim_mirror.scope import LABEL_MAX, STANDALONE_GROUP, InvalidScope, Scope


def test_a_scope_takes_a_url_safe_id_a_group_and_a_one_line_label() -> None:
    scope = Scope(id="ws:alpha:tp-1", group="alpha", label="alpha · tp-1")
    assert (scope.id, scope.group, scope.label) == ("ws:alpha:tp-1", "alpha", "alpha · tp-1")
    assert Scope.named("demo") == Scope(id="demo", group=STANDALONE_GROUP, label="demo")
    assert Scope.named("demo", group="team").group == "team"


@pytest.mark.parametrize(
    ("scope_id", "group", "label", "says"),
    [
        ("", "local", "x", "scope id"),
        ("-flag", "local", "x", "scope id"),
        ("a/b", "local", "x", "scope id"),
        ("a" * 129, "local", "x", "scope id"),
        ("ok", "has space", "x", "scope group"),
        ("ok", "local", "two\nlines", "one line"),
        ("ok", "local", "x" * (LABEL_MAX + 1), "one line"),
    ],
)
def test_what_cannot_be_used_is_refused(scope_id: str, group: str, label: str, says: str) -> None:
    with pytest.raises(InvalidScope, match=says):
        Scope(id=scope_id, group=group, label=label)


def test_a_folder_is_one_scope_by_its_real_path_whatever_it_is_called(tmp_path: Path) -> None:
    project = tmp_path / "My App (iOS)"
    project.mkdir()
    link = tmp_path / "link"
    link.symlink_to(project)
    scope = Scope.for_folder(project)
    assert scope.id.startswith("project-My-App-iOS-") and len(scope.id.rsplit("-", 1)[1]) == 8
    assert scope.label == "My App (iOS)" and scope.group == STANDALONE_GROUP
    assert Scope.for_folder(link) == scope
    other = tmp_path / "elsewhere" / "My App (iOS)"
    other.mkdir(parents=True)
    assert Scope.for_folder(other).id != scope.id


def test_a_folder_whose_name_has_nothing_usable_still_gets_a_scope(tmp_path: Path) -> None:
    odd = tmp_path / "🙂🙂"
    odd.mkdir()
    assert Scope.for_folder(odd).id.startswith("project-project-")
    assert Scope.for_folder(Path("/")).label == "root"
