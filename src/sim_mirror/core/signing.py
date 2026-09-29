# SPDX-License-Identifier: Apache-2.0
"""Which Apple development team signs for a scope's real device: WebDriverAgent's runner, and a build of a project that
names no team of its own.

The scope's project comes first: its own settings name the team it signs with (`build.xcodebuild.project_team`), and
that is the team Xcode registers the device with when the project is built for it -- so WebDriverAgent signed by the
same team runs there with nothing more to register, and several projects, each with its own team, use one device side
by side. Then ``real_devices.team_id``, for a device used outside any project or with a project that names none. Then
the one team this Mac's development certificates sign for, when there is exactly one. Otherwise there is none, and a
person says which.
"""

from __future__ import annotations

import dataclasses
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sim_mirror.build.xcodebuild import Project, project_team, projects
from sim_mirror.config.model import SimConfig
from sim_mirror.platform.keychain import Team, development_teams
from sim_mirror.protocol import TeamSource
from sim_mirror.scope import Scope

#: How long what this Mac's certificates sign for is kept before the keychain is read again.
MAC_TEAMS_S = 300.0

ListTeams = Callable[[], Awaitable[Sequence[Team]]]


@dataclass(frozen=True)
class SigningTeam:
    team: str
    source: TeamSource


def folder_team(folder: Path) -> str | None:
    """The team the projects at the top of a folder sign with: the one they name most, or None."""
    named = Counter(
        team
        for path in projects(folder)
        if (team := project_team(Project("-workspace" if path.suffix == ".xcworkspace" else "-project", path)))
    )
    return named.most_common(1)[0][0] if named else None


class SigningTeams:
    """The team each scope's real device is signed for. `folder_for` is the folder a scope builds in (the host's
    `Policy.build_folder`); `mac_teams` reads the keychain, and is asked at most every `MAC_TEAMS_S`."""

    def __init__(
        self,
        folder_for: Callable[[Scope], Path | None],
        mac_teams: ListTeams = development_teams,
        *,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._folder_for = folder_for
        self._mac_teams = mac_teams
        self._clock = clock
        self._now = now
        self._known: tuple[float, tuple[Team, ...]] | None = None

    async def of(self, scope: Scope, config: SimConfig) -> SigningTeam | None:
        folder = self._folder_for(scope)
        project = folder_team(folder) if folder is not None else None
        return SigningTeam(project, "project") if project else await self.outside_project(config)

    async def outside_project(self, config: SimConfig) -> SigningTeam | None:
        """The team for a device used outside any project: ``real_devices.team_id``, else this Mac's only team."""
        if config.real_devices_team_id:
            return SigningTeam(config.real_devices_team_id, "setting")
        valid = [team for team in await self.on_mac() if team.valid(self._now())]
        return SigningTeam(valid[0].team_id, "mac") if len(valid) == 1 else None

    async def on_mac(self) -> tuple[Team, ...]:
        """The teams this Mac's development certificates sign for."""
        if self._known is None or self._clock() - self._known[0] >= MAC_TEAMS_S:
            self._known = (self._clock(), tuple(await self._mac_teams()))
        return self._known[1]

    async def applied(self, scope: Scope, config: SimConfig) -> SimConfig:
        """The scope's settings with ``real_devices.team_id`` as the team its device is signed for -- empty when there
        is none -- which is what a real device's connector reads."""
        found = await self.of(scope, config)
        team = found.team if found else ""
        return config if team == config.real_devices_team_id else dataclasses.replace(config, real_devices_team_id=team)
