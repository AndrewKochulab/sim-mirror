# SPDX-License-Identifier: Apache-2.0
"""Which test targets a test run's test plan runs, read from its ``.xctestplan`` -- as xcodebuild does.

A scheme with test plans names them in its Test action (`schemes.plan_references`), one of them its default, and
each plan lists its test targets, some of them turned off (``"enabled" : false``). A run that names a plan
(``-testPlan``) runs that one, and a run that names none the default.

``xcodebuild -showBuildSettings test`` cannot say this: it lists the targets a plan turns off as well, and it leaves
``-testPlan`` out of account, answering for the default plan whichever is named (measured on Xcode 26.6: a target
every plan of the scheme turned off was listed, and a default plan with none on failed the call whichever plan was
named). So a test run held to `build.expected_bundle_id` reads the plan itself, and is not refused for a
target that will not run.

**How it is read**: a plan is JSON, read with the standard library, and no larger than any plan Xcode writes. Only
whether each target is turned on counts. A plan's ``selectedTests`` and ``skippedTests`` name tests inside a target,
never a whole target, so a target listed with them still runs. When the plan cannot be found or read, nothing is
known of it, and a run checks every test target, as it does for a scheme without plans.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from sim_mirror.build.schemes import plan_references, scheme_file

#: A plan Xcode writes for even a large project is a few kilobytes; a bigger file is not read.
PLAN_MAX_BYTES = 1_000_000
SUFFIX = ".xctestplan"


def _container(scheme: Path) -> Path | None:
    """The folder a scheme's ``container:`` references are relative to: the one holding its project or workspace."""
    for folder in scheme.parents:
        if folder.suffix in (".xcodeproj", ".xcworkspace"):
            return folder.parent
    return None


def plan_path(scheme: Path, reference: str) -> Path | None:
    """Where a test plan a scheme at `scheme` refers to is, or None for a reference of another kind."""
    kind, _, where = reference.partition(":")
    if not where.endswith(SUFFIX) or "\x00" in where:
        return None
    if kind == "absolute":
        return Path(where)
    container = _container(scheme) if kind == "container" else None
    return container / where if container is not None else None


def chosen_plan(references: Sequence[tuple[str, bool]], plan: str) -> str | None:
    """The reference of the plan a run uses: the one named `plan`, or the scheme's default when it names none -- or
    None when there is no such plan, or no single default."""
    if plan:
        named = [reference for reference, _ in references if Path(reference.partition(":")[2]).stem == plan]
        return named[0] if named else None
    defaults = [reference for reference, default in references if default]
    if len(defaults) == 1:
        return defaults[0]
    return references[0][0] if len(references) == 1 and not defaults else None


def enabled_targets(document: bytes) -> frozenset[str] | None:
    """The names of the test targets a plan turns on, or None when it is no plan Xcode writes."""
    try:
        plan = json.loads(document)
    except ValueError:
        return None
    entries = plan.get("testTargets") if isinstance(plan, dict) else None
    if not isinstance(entries, list):
        return None
    names = set()
    for entry in entries:
        target = entry.get("target") if isinstance(entry, dict) else None
        name = target.get("name") if isinstance(target, dict) else None
        enabled = entry.get("enabled", True) if isinstance(entry, dict) else None
        if not isinstance(name, str) or not isinstance(enabled, bool):
            return None
        if enabled:
            names.add(name)
    return frozenset(names)


def _read(path: Path) -> bytes | None:
    try:
        if not path.is_file() or path.stat().st_size > PLAN_MAX_BYTES:
            return None
        return path.read_bytes()
    except OSError:
        return None


def planned_targets(places: Sequence[Path], plan: str) -> frozenset[str] | None:
    """The test targets a test run of the scheme whose files may be at `places` runs, by the plan it names (`plan`) or
    the scheme's default when it names none (""). None when that is not known: the scheme has no plans, or the plan
    cannot be found or read."""
    found = scheme_file(places)
    if found is None:
        return None
    scheme, document = found
    reference = chosen_plan(plan_references(document), plan)
    path = plan_path(scheme, reference) if reference is not None else None
    read = _read(path) if path is not None else None
    return enabled_targets(read) if read is not None else None
