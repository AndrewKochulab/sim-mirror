# SPDX-License-Identifier: Apache-2.0
"""The daemon's credentials: an admin token made once and kept private, and scoped tokens kept only as digests."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from sim_mirror.daemon.tokens import ADMIN, TokenRefused, TokenStore, digest


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_the_admin_token_is_made_once_kept_private_and_matches_as_the_admin(tmp_path: Path) -> None:
    store = TokenStore(tmp_path / "secrets")
    token = store.admin_token()
    assert len(token) == 64 and store.admin_token() == token and mode(store.admin_path) == 0o600
    assert TokenStore(tmp_path / "secrets").admin_token() == token
    assert store.match(token) is ADMIN and ADMIN.covers("any-scope")
    assert store.match(None) is None and store.match("") is None and store.match("not-a-token") is None


def test_a_scoped_token_is_shown_once_kept_as_a_digest_and_matched_until_revoked(tmp_path: Path) -> None:
    store = TokenStore(tmp_path, clock=lambda: 1700.0)
    record, token = store.create("agent", ["tp-1", "tp-1", "tp-2"], label="Codex", roots=["/Users/me/Notes"])
    assert record.scopes == ("tp-1", "tp-2") and record.created == 1700.0 and record.digest == digest(token)
    stored = store.path.read_text()
    assert token not in stored and record.digest in stored and mode(store.path) == 0o600
    assert store.match(token) == record
    assert record.public() == {
        "id": record.id,
        "kind": "agent",
        "scopes": ["tp-1", "tp-2"],
        "label": "Codex",
        "roots": ["/Users/me/Notes"],
        "created": 1700.0,
        "host": "",
    }
    viewer, _ = store.create("viewer", ["*"], roots=["/Users/me/Ignored"])
    everywhere, _ = store.create("agent", ["*"], roots=["/Users/me/Kit", "/Users/me/Notes"])
    assert viewer.covers("tp-9") and not record.covers("tp-9")
    assert store.roots("tp-1") == (Path("/Users/me/Notes"), Path("/Users/me/Kit"))
    assert store.roots("tp-9") == (Path("/Users/me/Kit"), Path("/Users/me/Notes"))
    assert store.revoke(record.id) is True and store.match(token) is None and store.revoke(record.id) is False
    assert [kept.id for kept in store.records()] == [viewer.id, everywhere.id]


@pytest.mark.parametrize(
    ("kind", "scopes", "label", "roots", "message"),
    [
        ("root", ["tp-1"], "", [], "a token's kind is one of agent, viewer, admin, host"),
        ("host", ["notes:1"], "", [], "a host token is for namespaces only"),
        ("host", ["*"], "", [], "a host token is for namespaces only"),
        ("agent", ["no tes:*"], "", [], "one or more scope ids, namespaces such as notes:\\*"),
        ("agent", [], "", [], "one or more scope ids"),
        ("agent", ["bad id"], "", [], "one or more scope ids"),
        ("agent", ["tp-1"], "", ["relative/folder"], "a token's roots are absolute folders"),
        ("agent", ["tp-1"], "x" * 81, [], "one line of at most 80 characters"),
        ("agent", ["tp-1"], "two\nlines", [], "one line of at most 80 characters"),
    ],
)
def test_a_token_asked_for_wrongly_is_refused_and_nothing_is_written(
    tmp_path: Path, kind: str, scopes: list[str], label: str, roots: list[str], message: str
) -> None:
    store = TokenStore(tmp_path)
    with pytest.raises(TokenRefused, match=message):
        store.create(kind, scopes, label=label, roots=roots)
    assert not store.path.exists()


def test_a_tokens_file_that_cannot_be_read_accepts_no_scoped_token_and_odd_entries_are_left_out(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = TokenStore(tmp_path)
    record, token = store.create("viewer", ["tp-1"])
    store.path.write_text("{not json")
    assert store.match(token) is None and "cannot be read" in caplog.text
    good = record.stored()
    entries = [
        good,
        "not an entry",
        {**good, "kind": "root"},
        {**good, "id": 3},
        {**good, "scopes": "tp-1"},
        {**good, "roots": [1]},
        {**good, "id": "t-created", "created": True},
        {**good, "host": 7},
    ]
    store.path.write_text(json.dumps({"tokens": entries}))
    assert [kept.id for kept in store.records()] == [record.id, "t-created"]
    assert store.records()[1].created == 0.0
    store.path.write_text("[]")
    assert store.records() == []


def test_a_host_covers_its_namespaces_and_no_two_hosts_share_one(tmp_path: Path) -> None:
    store = TokenStore(tmp_path)
    notes, _ = store.create("host", ["notes:*", "notes-beta:*"], label="Notes", roots=[str(tmp_path / "Projects")])
    assert notes.namespaces == ("notes", "notes-beta") and notes.public()["host"] == ""
    assert notes.covers("notes:42") and notes.covers("notes-beta:1") and not notes.covers("notes")
    assert not notes.covers("notes2:1") and not notes.covers("tp-1")
    with pytest.raises(TokenRefused, match="another host already has the namespace notes"):
        store.create("host", ["mail:*", "notes:*"])
    assert store.host_of("notes-beta:7") == notes and store.host_of("tp-1") is None
    everything, _ = store.create("admin", ["*"])
    assert (
        store.host_of("notes:1") == notes and everything.covers("notes:1") and store.find(everything.id) == everything
    )
    assert store.find("t-none") is None


def test_a_host_makes_agent_and_viewer_tokens_only_within_its_namespaces_and_folders(tmp_path: Path) -> None:
    store = TokenStore(tmp_path)
    projects = tmp_path / "Projects"
    (projects / "App").mkdir(parents=True)
    notes, _ = store.create("host", ["notes:*"], roots=[str(projects)])
    agent, token = store.create("agent", ["notes:1"], roots=[str(projects / "App")], host=notes)
    viewer, _ = store.create("viewer", ["notes:*"], host=notes)
    assert (agent.host, viewer.host) == (notes.id, notes.id) and store.match(token) == agent
    assert store.made_by(notes) == [agent, viewer] and store.roots("notes:1") == (projects / "App",)
    refusals = [
        (("admin", ["notes:1"], ()), "a host makes agent and viewer tokens only"),
        (("host", ["other:*"], ()), "a host makes agent and viewer tokens only"),
        (("agent", ["*"], ()), r"\* is not in this host's namespaces: notes:\*"),
        (("agent", ["mail:*"], ()), r"mail:\* is not in this host's namespaces"),
        (("agent", ["tp-1"], ()), "tp-1 is not in this host's namespaces"),
        (("agent", ["notes:1"], (str(tmp_path / "Elsewhere"),)), "is not inside this host's folders"),
        (("agent", ["notes:1"], (str(projects / ".." / "Elsewhere"),)), "is not inside this host's folders"),
    ]
    for (kind, scopes, roots), message in refusals:
        with pytest.raises(TokenRefused, match=message):
            store.create(kind, scopes, roots=roots, host=notes)
    with pytest.raises(TokenRefused, match="only a host token makes tokens for its namespaces"):
        store.create("agent", ["notes:1"], host=agent)


def test_revoking_a_host_revokes_what_it_made_and_a_host_revokes_only_its_own(tmp_path: Path) -> None:
    store = TokenStore(tmp_path)
    notes, _ = store.create("host", ["notes:*"])
    mail, _ = store.create("host", ["mail:*"])
    _, agent_token = store.create("agent", ["notes:1"], host=notes)
    theirs, _ = store.create("viewer", ["mail:1"], host=mail)
    local, _ = store.create("viewer", ["tp-1"])
    assert store.revoke(theirs.id, host=notes) is False and store.revoke(local.id, host=notes) is False
    assert store.revoke("t-none", host=notes) is False
    assert store.revoke(notes.id) is True
    assert store.match(agent_token) is None and [record.id for record in store.records()] == [
        mail.id,
        theirs.id,
        local.id,
    ]
    with pytest.raises(TokenRefused, match="this host token has been revoked"):
        store.create("viewer", ["notes:1"], host=notes)
    assert store.revoke(theirs.id, host=mail) is True and store.records()[-1] == local
