# SPDX-License-Identifier: Apache-2.0
"""The development teams this Mac can sign with, read from its keychain's certificates -- nothing secret.

An "Apple Development" certificate names its team in its subject (the organizational unit is the team id, the
organization the team's name). ``security find-certificate -p`` prints certificates alone -- public, never a private
key -- so reading them asks for no password and changes nothing. Each is decoded by Python's own ``ssl``.
"""

from __future__ import annotations

import os
import ssl
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sim_mirror.platform import process
from sim_mirror.platform.process import Runner

#: The certificates a development team signs apps for devices with.
COMMON_NAME = "Apple Development"
END = "-----END CERTIFICATE-----"

Decode = Callable[[str], dict[str, Any]]


@dataclass(frozen=True)
class Team:
    """A team a certificate on this Mac signs for."""

    team_id: str
    name: str
    #: When its newest certificate here expires.
    expires: datetime

    def valid(self, now: datetime) -> bool:
        return self.expires > now


def decode_pem(pem: str) -> dict[str, Any]:
    """A certificate's subject and dates, as Python's ``ssl`` decodes them from a PEM file."""
    fd, path = tempfile.mkstemp(suffix=".pem")
    try:
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(pem)
        decoded: dict[str, Any] = ssl._ssl._test_decode_cert(path)  # type: ignore[attr-defined]
        return decoded
    finally:
        os.unlink(path)


def teams_in(pems: Sequence[str], decode: Decode = decode_pem) -> list[Team]:
    """The teams some PEM certificates sign for, each once with its latest expiry, by team id."""
    found: dict[str, Team] = {}
    for pem in pems:
        try:
            decoded = decode(pem)
            subject = {key: value for part in decoded["subject"] for key, value in part}
            expires = datetime.strptime(decoded["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        except (ssl.SSLError, OSError, KeyError, TypeError, ValueError):
            continue
        team_id, name = subject.get("organizationalUnitName"), subject.get("organizationName")
        if not isinstance(team_id, str) or not team_id:
            continue
        known = found.get(team_id)
        if known is None or known.expires < expires:
            found[team_id] = Team(team_id, str(name or team_id), expires)
    return sorted(found.values(), key=lambda team: team.team_id)


async def development_teams(run: Runner = process.run, decode: Decode = decode_pem) -> list[Team]:
    """The teams this Mac's development certificates sign for; none when the keychain cannot be read."""
    code, out = await run(("security", "find-certificate", "-a", "-c", COMMON_NAME, "-p"))
    if code != 0:
        return []
    pems = [block.strip() + "\n" + END + "\n" for block in out.split(END) if "BEGIN CERTIFICATE" in block]
    return teams_in(pems, decode)
