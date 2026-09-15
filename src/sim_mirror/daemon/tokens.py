# SPDX-License-Identifier: Apache-2.0
"""The daemon's credentials: one admin token, and scoped tokens kept only as digests.

The **admin token** is made on the daemon's first start, in the state folder, readable only by its owner
(`storage.private`). Whoever can read it is the person who installed SimMirror -- the CLI reads it to mint what it needs
-- so it may do everything: make and revoke tokens, reload settings, make login codes.

A **scoped token** is for one kind of client and a list of scopes (``*`` for all):

* ``agent`` -- an MCP relay calling tools for its scopes; it may name the folders its builds and installs may reach;
* ``viewer`` -- a page showing a scope's screen;
* ``admin`` -- a second admin credential, for a host application's backend.

Only a token's SHA-256 is kept (``tokens.json``, 0600): a token is shown once, when it is made, and compared in constant
time. A file that cannot be read accepts no scoped token at all, rather than guessing.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from sim_mirror.scope import ID_PATTERN
from sim_mirror.storage.private import file_lock, read_or_create_secret, write_private

logger = logging.getLogger(__name__)

ADMIN_TOKEN_FILE = "token"
TOKENS_FILE = "tokens.json"
ALL_SCOPES = "*"
LABEL_MAX = 80
Kind = Literal["agent", "viewer", "admin"]
KINDS: tuple[Kind, ...] = ("agent", "viewer", "admin")


class TokenRefused(ValueError):
    """A token that will not be made, said why."""


def digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _strings(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


@dataclass(frozen=True)
class TokenRecord:
    id: str
    kind: Kind
    scopes: tuple[str, ...]
    label: str = ""
    roots: tuple[str, ...] = ()
    created: float = 0.0
    digest: str = ""

    def covers(self, scope_id: str) -> bool:
        return ALL_SCOPES in self.scopes or scope_id in self.scopes

    def public(self) -> dict[str, Any]:
        """The record as it may be shown: everything but its digest."""
        return {
            "id": self.id,
            "kind": self.kind,
            "scopes": list(self.scopes),
            "label": self.label,
            "roots": list(self.roots),
            "created": self.created,
        }

    def stored(self) -> dict[str, Any]:
        return {**self.public(), "digest": self.digest}

    @classmethod
    def read(cls, entry: object) -> TokenRecord | None:
        """A record from the file, or None for one that is not a record."""
        if not isinstance(entry, dict):
            return None
        kind, scopes, roots = entry.get("kind"), entry.get("scopes"), entry.get("roots", [])
        token_id, label, stored, created = (
            entry.get("id"),
            entry.get("label", ""),
            entry.get("digest"),
            entry.get("created", 0),
        )
        if kind not in KINDS or not _strings(scopes) or not _strings(roots):
            return None
        if not (isinstance(token_id, str) and isinstance(label, str) and isinstance(stored, str)):
            return None
        when = float(created) if isinstance(created, (int, float)) and not isinstance(created, bool) else 0.0
        return cls(token_id, kind, tuple(cast(list[str], scopes)), label, tuple(cast(list[str], roots)), when, stored)


#: The admin token, as the record a request carrying it is let in with.
ADMIN = TokenRecord(id="admin", kind="admin", scopes=(ALL_SCOPES,), label="the admin token")


class TokenStore:
    def __init__(self, folder: Path, *, clock: Callable[[], float] = time.time) -> None:
        self._folder = folder
        self._clock = clock
        self._admin: str | None = None

    @property
    def admin_path(self) -> Path:
        return self._folder / ADMIN_TOKEN_FILE

    @property
    def path(self) -> Path:
        return self._folder / TOKENS_FILE

    def admin_token(self) -> str:
        """The admin token, made on first use."""
        if self._admin is None:
            self._admin = read_or_create_secret(self.admin_path).hex()
        return self._admin

    def records(self) -> list[TokenRecord]:
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError):
            logger.warning("%s cannot be read; no scoped token is accepted until it is replaced", self.path)
            return []
        entries = document.get("tokens") if isinstance(document, dict) else None
        return [record for record in map(TokenRecord.read, entries if isinstance(entries, list) else []) if record]

    def create(
        self, kind: str, scopes: Iterable[str], *, label: str = "", roots: Iterable[str] = ()
    ) -> tuple[TokenRecord, str]:
        """A new token of this kind for these scopes: its record, and the token itself -- shown this once."""
        if kind not in KINDS:
            raise TokenRefused(f"a token's kind is one of {', '.join(KINDS)}")
        chosen = tuple(dict.fromkeys(scopes))
        if not chosen or not all(scope == ALL_SCOPES or ID_PATTERN.match(scope) for scope in chosen):
            raise TokenRefused("a token is for one or more scope ids, or * for every scope")
        folders = tuple(dict.fromkeys(roots))
        if not all(Path(folder).is_absolute() for folder in folders):
            raise TokenRefused("a token's roots are absolute folders")
        if len(label) > LABEL_MAX or any(character in label for character in "\n\r\x00"):
            raise TokenRefused(f"a token's label is one line of at most {LABEL_MAX} characters")
        token = secrets.token_urlsafe(32)
        record = TokenRecord(
            id=f"t{secrets.token_hex(4)}",
            kind=kind,
            scopes=chosen,
            label=label,
            roots=folders,
            created=self._clock(),
            digest=digest(token),
        )
        with file_lock(self._lock_path()):
            self._write([*self.records(), record])
        return record, token

    def revoke(self, token_id: str) -> bool:
        """Stop accepting a token. Answers whether there was one."""
        with file_lock(self._lock_path()):
            records = self.records()
            kept = [record for record in records if record.id != token_id]
            if len(kept) == len(records):
                return False
            self._write(kept)
        return True

    def match(self, presented: str | None) -> TokenRecord | None:
        """The record a presented token belongs to, or None."""
        if not presented:
            return None
        if hmac.compare_digest(presented.encode("utf-8"), self.admin_token().encode("utf-8")):
            return ADMIN
        wanted = digest(presented)
        return next((record for record in self.records() if hmac.compare_digest(record.digest, wanted)), None)

    def roots(self, scope_id: str) -> tuple[Path, ...]:
        """The folders agent tokens for this scope named, in the order they were given."""
        found: dict[str, None] = {}
        for record in self.records():
            if record.kind == "agent" and record.covers(scope_id):
                found.update(dict.fromkeys(record.roots))
        return tuple(Path(folder) for folder in found)

    def _lock_path(self) -> Path:
        return self._folder / f"{TOKENS_FILE}.lock"

    def _write(self, records: list[TokenRecord]) -> None:
        document = {"tokens": [record.stored() for record in records]}
        write_private(self.path, (json.dumps(document, indent=2) + "\n").encode("utf-8"))
