# SPDX-License-Identifier: Apache-2.0
"""The team that signs for a scope's real device: its project's own, then the setting, then this Mac's only team."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sim_mirror.config.model import SimConfig
from sim_mirror.core.signing import MAC_TEAMS_S, SigningTeam, SigningTeams, folder_team
from sim_mirror.platform.keychain import Team
from sim_mirror.scope import Scope
from sim_mirror.testing.fakes import ManualClock

NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)
FAR = datetime(2126, 1, 1, tzinfo=timezone.utc)
CONFIG = SimConfig.defaults()
PROJECT, ELSEWHERE = Scope.named("notes"), Scope.named("elsewhere")


def project(folder: Path, *teams: str, name: str = "Notes") -> Path:
    """A project at the top of `folder` whose targets name these teams."""
    path = folder / f"{name}.xcodeproj"
    path.mkdir(parents=True, exist_ok=True)
    (path / "project.pbxproj").write_text("".join(f"DEVELOPMENT_TEAM = {team};\n" for team in teams))
    return path


class Mac:
    """The teams this Mac's certificates sign for, and how often they were read."""

    def __init__(self, *teams: Team) -> None:
        self.teams = list(teams)
        self.read = 0

    async def __call__(self) -> list[Team]:
        self.read += 1
        return self.teams


def signing(folder: Path, mac: Mac, clock: ManualClock | None = None) -> SigningTeams:
    folders = {PROJECT.id: folder}
    return SigningTeams(lambda scope: folders.get(scope.id), mac, clock=clock or ManualClock(0.0), now=lambda: NOW)


async def test_the_project_s_own_team_comes_first_then_the_setting_then_the_only_team_this_mac_signs_for(
    tmp_path: Path,
) -> None:
    project(tmp_path, "PROJTEAM01")
    mac = Mac(Team("MACTEAM001", "Me", FAR))
    teams = signing(tmp_path, mac)
    named = CONFIG.with_values(real_devices_team_id="SETTEAM001")
    assert await teams.of(PROJECT, named) == SigningTeam("PROJTEAM01", "project")
    assert await teams.of(ELSEWHERE, named) == SigningTeam("SETTEAM001", "setting")
    assert await teams.of(ELSEWHERE, CONFIG) == SigningTeam("MACTEAM001", "mac")
    assert mac.read == 1, "the keychain is read only when neither the project nor the setting names a team"


async def test_several_teams_or_only_expired_ones_leave_it_to_a_person(tmp_path: Path) -> None:
    several = signing(tmp_path, Mac(Team("TEAMAAAAA1", "One", FAR), Team("TEAMBBBBB2", "Two", FAR)))
    assert await several.of(ELSEWHERE, CONFIG) is None
    expired = signing(tmp_path, Mac(Team("TEAMAAAAA1", "One", datetime(2020, 1, 1, tzinfo=timezone.utc))))
    assert await expired.of(ELSEWHERE, CONFIG) is None
    assert await signing(tmp_path, Mac()).of(PROJECT, CONFIG) is None, "a folder with no project names no team"


async def test_what_this_mac_signs_for_is_read_again_only_after_a_while(tmp_path: Path) -> None:
    clock = ManualClock(0.0)
    mac = Mac(Team("MACTEAM001", "Me", FAR))
    teams = signing(tmp_path, mac, clock)
    await teams.on_mac()
    clock.advance(MAC_TEAMS_S - 1)
    await teams.on_mac()
    assert mac.read == 1
    clock.advance(1)
    assert await teams.on_mac() == (Team("MACTEAM001", "Me", FAR),) and mac.read == 2


async def test_the_settings_a_real_device_is_attached_with_carry_the_team(tmp_path: Path) -> None:
    project(tmp_path, "PROJTEAM01")
    teams = signing(tmp_path, Mac())
    applied = await teams.applied(PROJECT, CONFIG)
    assert applied.real_devices_team_id == "PROJTEAM01" and applied.real_devices_screen == CONFIG.real_devices_screen
    already = CONFIG.with_values(real_devices_team_id="PROJTEAM01")
    assert await teams.applied(PROJECT, already) is already
    assert (await teams.applied(ELSEWHERE, CONFIG)) is CONFIG, "none known: empty, as it was"


def test_a_folder_s_team_is_the_one_most_of_its_projects_sign_with(tmp_path: Path) -> None:
    assert folder_team(tmp_path) is None
    project(tmp_path, "TEAMAAAAA1", "TEAMAAAAA1", name="App")
    project(tmp_path, "TEAMBBBBB2", name="Widget")
    project(tmp_path, "TEAMBBBBB2", name="Extension")
    assert folder_team(tmp_path) == "TEAMBBBBB2"
    project(tmp_path / "bare", name="Bare")
    assert folder_team(tmp_path / "bare") is None, "a project that names no team"
