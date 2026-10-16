# SPDX-License-Identifier: Apache-2.0
"""The configuration a scheme's own action builds with: where its file is, and what is read from it."""

from __future__ import annotations

import getpass
from pathlib import Path

import pytest

from sim_mirror.build import schemes
from sim_mirror.build.schemes import action_configuration, scheme_configuration, scheme_files
from sim_mirror.testing.fakes import fixture

STAGING = fixture("xcscheme-staging.xcscheme")


def scheme(*actions: str) -> bytes:
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<Scheme version = "1.7">{"".join(actions)}</Scheme>'.encode()


def put(place: Path, text: str | bytes = STAGING) -> Path:
    place.parent.mkdir(parents=True, exist_ok=True)
    place.write_bytes(text.encode() if isinstance(text, str) else text)
    return place


def test_a_scheme_xcode_wrote_runs_in_its_run_actions_configuration_and_tests_in_its_test_actions() -> None:
    assert action_configuration(STAGING.encode(), "LaunchAction") == "Staging"
    assert action_configuration(STAGING.encode(), "TestAction") == "Debug"
    assert action_configuration(STAGING.encode(), "ArchiveAction") == "Release"
    assert action_configuration(scheme('<TestAction buildConfiguration = "App &amp; Beta"/>'), "TestAction") == (
        "App & Beta"
    )


@pytest.mark.parametrize(
    "document",
    [
        scheme("<LaunchAction/>"),
        scheme('<LaunchAction buildConfiguration = ""/>'),
        scheme('<LaunchAction buildConfiguration = "-derivedDataPath"/>'),
        scheme('<LaunchAction buildConfiguration = "Debug&#10;"/>'),
        # Only the scheme's own action counts, not an element of that name deeper inside another.
        scheme('<BuildAction><LaunchAction buildConfiguration = "Staging"/></BuildAction>'),
        b'<Workspace><LaunchAction buildConfiguration = "Staging"/></Workspace>',
        b'<Scheme><LaunchAction buildConfiguration = "Staging">',
        b"not xml",
        # No scheme declares an entity: one that does is not read at all, so nothing in it can expand.
        b'<?xml version="1.0"?><!DOCTYPE Scheme [<!ENTITY a "Staging">]>'
        b'<Scheme><LaunchAction buildConfiguration = "&a;"/></Scheme>',
        b'<?xml version="1.0"?><!DOCTYPE Scheme [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]>'
        b'<Scheme><LaunchAction buildConfiguration = "&b;"/></Scheme>',
        b'<?xml version="1.0"?><!DOCTYPE Scheme [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        b'<Scheme><LaunchAction buildConfiguration = "&x;"/></Scheme>',
    ],
)
def test_a_scheme_that_names_no_configuration_a_call_could_give_says_none(document: bytes) -> None:
    assert action_configuration(document, "LaunchAction") is None


def test_the_first_action_of_its_kind_is_the_one() -> None:
    twice = scheme('<LaunchAction buildConfiguration = "Staging"/>', '<LaunchAction buildConfiguration = "Beta"/>')
    assert action_configuration(twice, "LaunchAction") == "Staging"


def test_a_scheme_is_looked_for_where_xcode_keeps_it_shared_first_then_the_persons_own(tmp_path: Path) -> None:
    workspace, project = tmp_path / "App.xcworkspace", tmp_path / "App.xcodeproj"
    assert scheme_files([workspace, project], "App (Staging)", user="dev") == [
        workspace / "xcshareddata/xcschemes/App (Staging).xcscheme",
        workspace / "xcuserdata/dev.xcuserdatad/xcschemes/App (Staging).xcscheme",
        project / "xcshareddata/xcschemes/App (Staging).xcscheme",
        project / "xcuserdata/dev.xcuserdatad/xcschemes/App (Staging).xcscheme",
    ]
    assert scheme_files([project], "App")[1].parent.parent.name == f"{getpass.getuser()}.xcuserdatad"
    assert scheme_files([project], "../../App") == [] and scheme_files([project], ".hidden") == []


def test_the_first_scheme_file_there_is_decides_even_when_it_names_nothing(tmp_path: Path) -> None:
    project = tmp_path / "App.xcodeproj"
    places = scheme_files([project], "App", user="dev")
    assert scheme_configuration(places, "build") is None
    own = put(places[1], scheme('<LaunchAction buildConfiguration = "Mine"/>'))
    assert scheme_configuration(places, "build") == "Mine"
    put(places[0])
    assert scheme_configuration(places, "build") == "Staging" and scheme_configuration(places, "test") == "Debug"
    put(places[0], scheme("<LaunchAction/>"))
    assert scheme_configuration(places, "build") is None and own.exists()


def test_another_persons_schemes_are_theirs_and_are_not_read(tmp_path: Path) -> None:
    project = tmp_path / "App.xcodeproj"
    put(project / "xcuserdata/someone.xcuserdatad/xcschemes/App.xcscheme")
    assert scheme_configuration(scheme_files([project], "App", user="dev"), "build") is None


def test_a_scheme_file_that_cannot_be_read_or_is_too_big_is_passed_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "App.xcodeproj"
    shared, own = scheme_files([project], "App", user="dev")
    shared.mkdir(parents=True)
    put(own)
    assert scheme_configuration([shared, own], "build") == "Staging"
    monkeypatch.setattr(schemes, "SCHEME_MAX_BYTES", 100)
    assert scheme_configuration([own], "build") is None
    monkeypatch.undo()

    def unreadable(self: Path) -> bytes:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "read_bytes", unreadable)
    assert scheme_configuration([own], "build") is None
