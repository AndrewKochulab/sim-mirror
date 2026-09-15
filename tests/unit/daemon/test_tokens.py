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
        ("root", ["tp-1"], "", [], "a token's kind is one of agent, viewer, admin"),
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
    ]
    store.path.write_text(json.dumps({"tokens": entries}))
    assert [kept.id for kept in store.records()] == [record.id, "t-created"]
    assert store.records()[1].created == 0.0
    store.path.write_text("[]")
    assert store.records() == []
