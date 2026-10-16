# SPDX-License-Identifier: Apache-2.0
"""Which test targets a test run's plan runs: the plan it names or the scheme's default, read from its file."""

from __future__ import annotations

from pathlib import Path

import pytest

from sim_mirror.build import xctestplan
from sim_mirror.build.schemes import plan_references, scheme_files
from sim_mirror.build.xctestplan import chosen_plan, enabled_targets, plan_path, planned_targets
from sim_mirror.testing.fakes import fixture

SCHEME = fixture("xcscheme-test-plans.xcscheme")
UNITS_ONLY = fixture("xctestplan-units-only.xctestplan")
EVERYTHING = fixture("xctestplan-everything.xctestplan")
ALL = frozenset({"NotesProbeTests", "NotesProbeUITests", "NotesModelTests"})


def put(place: Path, text: str) -> Path:
    place.parent.mkdir(parents=True, exist_ok=True)
    place.write_text(text, encoding="utf-8")
    return place


def project_with_plans(folder: Path) -> list[Path]:
    """A project whose shared scheme has the fixture's two plans beside it: answers where its scheme may be."""
    places = scheme_files([folder / "NotesProbe.xcodeproj"], "NotesProbe", user="dev")
    put(places[0], SCHEME)
    put(folder / "UnitsOnly.xctestplan", UNITS_ONLY)
    put(folder / "Everything.xctestplan", EVERYTHING)
    return places


def test_a_scheme_xcode_wrote_names_its_plans_and_which_is_its_default() -> None:
    assert plan_references(SCHEME.encode()) == [
        ("container:UnitsOnly.xctestplan", True),
        ("container:Everything.xctestplan", False),
    ]
    assert plan_references(fixture("xcscheme-staging.xcscheme").encode()) == []
    assert plan_references(b"not xml") == []
    nested = b'<Scheme><TestAction><Testables><TestPlanReference reference="container:A.xctestplan"/></Testables>'
    assert plan_references(nested + b"</TestAction></Scheme>") == []


def test_a_plan_xcode_wrote_turns_on_every_target_it_does_not_turn_off() -> None:
    assert enabled_targets(UNITS_ONLY.encode()) == {"NotesProbeTests", "NotesModelTests"}
    assert enabled_targets(EVERYTHING.encode()) == ALL
    assert enabled_targets(b'{"testTargets": []}') == frozenset()


@pytest.mark.parametrize(
    "document",
    [
        b"not json",
        b"[]",
        b'{"version": 1}',
        b'{"testTargets": {}}',
        b'{"testTargets": ["NotesProbeTests"]}',
        b'{"testTargets": [{"target": "NotesProbeTests"}]}',
        b'{"testTargets": [{"target": {"name": 1}}]}',
        b'{"testTargets": [{"enabled": "NO", "target": {"name": "NotesProbeTests"}}]}',
    ],
)
def test_a_file_that_is_no_plan_xcode_writes_says_nothing_of_its_targets(document: bytes) -> None:
    assert enabled_targets(document) is None


def test_a_run_uses_the_plan_it_names_or_else_the_schemes_default() -> None:
    references = [("container:UnitsOnly.xctestplan", True), ("container:Tests/Everything.xctestplan", False)]
    assert chosen_plan(references, "") == "container:UnitsOnly.xctestplan"
    assert chosen_plan(references, "Everything") == "container:Tests/Everything.xctestplan"
    assert chosen_plan(references, "Gone") is None
    assert chosen_plan([("container:Only.xctestplan", False)], "") == "container:Only.xctestplan"
    assert chosen_plan([(name, False) for name, _ in references], "") is None, "no default to go by"
    assert chosen_plan([(name, True) for name, _ in references], "") is None, "two defaults"
    assert chosen_plan([], "") is None


def test_a_plan_is_where_its_reference_says_beside_the_project_or_workspace(tmp_path: Path) -> None:
    shared = tmp_path / "App.xcodeproj/xcshareddata/xcschemes/App.xcscheme"
    own = tmp_path / "App.xcworkspace/xcuserdata/dev.xcuserdatad/xcschemes/App.xcscheme"
    assert plan_path(shared, "container:Plans/App.xctestplan") == tmp_path / "Plans/App.xctestplan"
    assert plan_path(own, "container:App.xctestplan") == tmp_path / "App.xctestplan"
    assert plan_path(shared, "absolute:/Plans/App.xctestplan") == Path("/Plans/App.xctestplan")
    assert plan_path(shared, "group:App.xctestplan") is None
    assert plan_path(shared, "container:App.json") is None
    assert plan_path(shared, "container:App\x00.xctestplan") is None
    assert plan_path(tmp_path / "App.xcscheme", "container:App.xctestplan") is None


def test_a_runs_targets_are_its_named_plans_or_its_schemes_default(tmp_path: Path) -> None:
    places = project_with_plans(tmp_path)
    assert planned_targets(places, "") == {"NotesProbeTests", "NotesModelTests"}
    assert planned_targets(places, "Everything") == ALL


def test_a_plan_that_cannot_be_found_or_read_says_nothing_so_every_target_is_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    places = project_with_plans(tmp_path)
    assert planned_targets(places, "Gone") is None
    assert planned_targets(scheme_files([tmp_path / "Other.xcodeproj"], "NotesProbe", user="dev"), "") is None
    put(places[0], fixture("xcscheme-staging.xcscheme"))
    assert planned_targets(places, "") is None, "a scheme without plans"
    put(places[0], SCHEME)
    (tmp_path / "UnitsOnly.xctestplan").write_text("{", encoding="utf-8")
    assert planned_targets(places, "") is None
    (tmp_path / "UnitsOnly.xctestplan").unlink()
    (tmp_path / "UnitsOnly.xctestplan").mkdir()
    assert planned_targets(places, "") is None
    monkeypatch.setattr(xctestplan, "PLAN_MAX_BYTES", 100)
    assert planned_targets(places, "Everything") is None
    monkeypatch.undo()

    read_bytes = Path.read_bytes

    def unreadable(self: Path) -> bytes:
        if self.suffix == ".xctestplan":
            raise PermissionError(13, "Permission denied")
        return read_bytes(self)

    assert planned_targets(places, "Everything") == ALL
    monkeypatch.setattr(Path, "read_bytes", unreadable)
    assert planned_targets(places, "Everything") is None
