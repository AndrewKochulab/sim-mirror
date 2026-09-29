# SPDX-License-Identifier: Apache-2.0
"""The development teams this Mac signs with, read from its certificates alone."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from sim_mirror.platform.keychain import Team, decode_pem, development_teams, teams_in
from sim_mirror.testing.fakes import fixture

PEM = fixture("development-certificate.pem")


def test_a_certificate_names_its_team_and_when_it_expires() -> None:
    assert decode_pem(PEM)["notAfter"].endswith("2126 GMT")
    (team,) = teams_in([PEM])
    assert (team.team_id, team.name) == ("TESTTEAM01", "Test Team")
    assert team.valid(datetime(2030, 1, 1, tzinfo=timezone.utc)) and not team.valid(
        datetime(2127, 1, 1, tzinfo=timezone.utc)
    )


def subject(team: str | None, name: str | None, until: str) -> dict[str, Any]:
    parts: list[tuple[tuple[str, str], ...]] = [(("commonName", "Apple Development: Test"),)]
    parts += [(("organizationalUnitName", team),)] if team else []
    parts += [(("organizationName", name),)] if name else []
    return {"subject": tuple(parts), "notAfter": until}


def test_each_team_is_listed_once_with_its_latest_certificate_and_unreadable_ones_are_skipped() -> None:
    answers = {
        "old": subject("AAAAAAAAAA", "Alpha", "Jan  1 00:00:00 2027 GMT"),
        "new": subject("AAAAAAAAAA", "Alpha", "Jan  1 00:00:00 2028 GMT"),
        "older": subject("AAAAAAAAAA", "Alpha", "Jan  1 00:00:00 2026 GMT"),
        "bare": subject("BBBBBBBBBB", None, "Jan  1 00:00:00 2027 GMT"),
        "teamless": subject(None, "Nobody", "Jan  1 00:00:00 2027 GMT"),
        "undated": subject("CCCCCCCCCC", "Gamma", "someday"),
        "broken": {},
    }
    teams = teams_in(list(answers), answers.__getitem__)
    assert teams == [
        Team("AAAAAAAAAA", "Alpha", datetime(2028, 1, 1, tzinfo=timezone.utc)),
        Team("BBBBBBBBBB", "BBBBBBBBBB", datetime(2027, 1, 1, tzinfo=timezone.utc)),
    ]


async def test_the_keychain_is_asked_for_development_certificates_only() -> None:
    seen: list[Sequence[str]] = []

    async def run(argv: Sequence[str]) -> tuple[int, str]:
        seen.append(argv)
        return 0, f"{PEM}\n{PEM}"

    assert [team.team_id for team in await development_teams(run)] == ["TESTTEAM01"]
    assert seen == [("security", "find-certificate", "-a", "-c", "Apple Development", "-p")]

    async def locked(argv: Sequence[str]) -> tuple[int, str]:
        return 44, "security: SecKeychainSearchCopyNext: The specified item could not be found in the keychain."

    assert await development_teams(locked) == []
