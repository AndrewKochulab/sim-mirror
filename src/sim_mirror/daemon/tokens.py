# SPDX-License-Identifier: Apache-2.0
"""The daemon's credentials: one admin token, and scoped tokens kept only as digests.

The **admin token** is made on the daemon's first start, in the state folder, readable only by its owner
(`storage.private`). Whoever can read it is the person who installed SimMirror -- the CLI reads it to mint what it needs
-- so it may do everything: make and revoke tokens, reload settings, make login codes.

A **scoped token** is for one kind of client and a list of scopes: scope ids, ``*`` for all, or a namespace --
``notes:*``, every scope whose id starts ``notes:``:

* ``agent`` -- an MCP relay calling tools for its scopes; it may name the folders its builds and installs may reach;
* ``viewer`` -- a page showing a scope's screen;
* ``admin`` -- a second admin credential, for a backend trusted with everything;
* ``host`` -- an application sharing this daemon with others. It is for namespaces only, and no two hosts share one.
  In its namespaces it may do what a viewer may, and make, list and revoke ``agent`` and ``viewer`` tokens of its own
  -- for scopes it covers, naming only folders inside its own. Revoking a host revokes every token it made.

Only a token's SHA-256 is kept (``tokens.json``, 0600): a token is shown once, when it is made, and compared in constant
time. A file that cannot be read accepts no scoped token at all, rather than guessing.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
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
Kind = Literal["agent", "viewer", "admin", "host"]
KINDS: tuple[Kind, ...] = ("agent", "viewer", "admin", "host")
#: The kinds of token a host may make.
HOST_MADE: tuple[Kind, ...] = ("agent", "viewer")
#: A namespace: ``name:*``, every scope whose id starts with ``name:``.
NAMESPACE = re.compile(r"\A([A-Za-z0-9][A-Za-z0-9_.-]{0,63}):\*\Z")


class TokenRefused(ValueError):
    """A token that will not be made, said why."""


def digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _strings(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def namespace_of(scope: str) -> str | None:
    """The namespace a scope entry names -- ``notes`` for ``notes:*`` -- or None when it names none."""
    found = NAMESPACE.match(scope)
    return found[1] if found else None


def _covered(entry: str, scope_id: str) -> bool:
    if entry in (ALL_SCOPES, scope_id):
        return True
    namespace = namespace_of(entry)
    return namespace is not None and scope_id.startswith(f"{namespace}:")


def _inside(folder: str, roots: tuple[str, ...]) -> bool:
    real = Path(folder).resolve()
    return any(real.is_relative_to(Path(root).resolve()) for root in roots)


@dataclass(frozen=True)
class TokenRecord:
    id: str
    kind: Kind
    scopes: tuple[str, ...]
    label: str = ""
    roots: tuple[str, ...] = ()
    created: float = 0.0
    digest: str = ""
    #: The host token that made it; empty for one the admin made.
    host: str = ""

    def covers(self, scope_id: str) -> bool:
        return any(_covered(entry, scope_id) for entry in self.scopes)

    @property
    def namespaces(self) -> tuple[str, ...]:
        return tuple(name for entry in self.scopes if (name := namespace_of(entry)) is not None)

    def public(self) -> dict[str, Any]:
        """The record as it may be shown: everything but its digest."""
        return {
            "id": self.id,
            "kind": self.kind,
            "scopes": list(self.scopes),
            "label": self.label,
            "roots": list(self.roots),
            "created": self.created,
            "host": self.host,
        }

    def stored(self) -> dict[str, Any]:
        return {**self.public(), "digest": self.digest}

    @classmethod
    def read(cls, entry: object) -> TokenRecord | None:
        """A record from the file, or None for one that is not a record."""
        if not isinstance(entry, dict):
            return None
        kind, scopes, roots = entry.get("kind"), entry.get("scopes"), entry.get("roots", [])
        token_id, label, stored, created, host = (
            entry.get("id"),
            entry.get("label", ""),
            entry.get("digest"),
            entry.get("created", 0),
            entry.get("host", ""),
        )
        if kind not in KINDS or not _strings(scopes) or not _strings(roots):
            return None
        if not all(isinstance(text, str) for text in (token_id, label, stored, host)):
            return None
        when = float(created) if isinstance(created, (int, float)) and not isinstance(created, bool) else 0.0
        return cls(
            cast(str, token_id),
            kind,
            tuple(cast(list[str], scopes)),
            cast(str, label),
            tuple(cast(list[str], roots)),
            when,
            cast(str, stored),
            cast(str, host),
        )


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
        self,
        kind: str,
        scopes: Iterable[str],
        *,
        label: str = "",
        roots: Iterable[str] = (),
        host: TokenRecord | None = None,
    ) -> tuple[TokenRecord, str]:
        """A new token of this kind for these scopes: its record, and the token itself -- shown this once.

        Made by a `host`, it is one of the kinds a host may make, for scopes the host covers, naming folders inside
        the host's own.
        """
        if kind not in KINDS:
            raise TokenRefused(f"a token's kind is one of {', '.join(KINDS)}")
        chosen = tuple(dict.fromkeys(scopes))
        if not chosen or not all(
            scope == ALL_SCOPES or ID_PATTERN.match(scope) or namespace_of(scope) for scope in chosen
        ):
            raise TokenRefused("a token is for one or more scope ids, namespaces such as notes:*, or * for every scope")
        folders = tuple(dict.fromkeys(roots))
        if not all(Path(folder).is_absolute() for folder in folders):
            raise TokenRefused("a token's roots are absolute folders")
        if len(label) > LABEL_MAX or any(character in label for character in "\n\r\x00"):
            raise TokenRefused(f"a token's label is one line of at most {LABEL_MAX} characters")
        if kind == "host" and not all(namespace_of(scope) for scope in chosen):
            raise TokenRefused("a host token is for namespaces only, such as notes:*")
        if host is not None:
            self._within(host, kind, chosen, folders)
        token = secrets.token_urlsafe(32)
        record = TokenRecord(
            id=f"t{secrets.token_hex(4)}",
            kind=kind,
            scopes=chosen,
            label=label,
            roots=folders,
            created=self._clock(),
            digest=digest(token),
            host=host.id if host is not None else "",
        )
        with file_lock(self._lock_path()):
            records = self.records()
            if host is not None and not any(each.id == host.id for each in records):
                raise TokenRefused("this host token has been revoked")
            taken = {name for each in records if each.kind == "host" for name in each.namespaces}
            claimed = taken & set(record.namespaces) if kind == "host" else set()
            if claimed:
                raise TokenRefused(f"another host already has the namespace {sorted(claimed)[0]}")
            self._write([*records, record])
        return record, token

    @staticmethod
    def _within(host: TokenRecord, kind: str, scopes: tuple[str, ...], folders: tuple[str, ...]) -> None:
        if host.kind != "host":
            raise TokenRefused("only a host token makes tokens for its namespaces")
        if kind not in HOST_MADE:
            raise TokenRefused(f"a host makes {' and '.join(HOST_MADE)} tokens only")
        for scope in scopes:
            namespace = namespace_of(scope)
            inside = namespace in host.namespaces if namespace else scope != ALL_SCOPES and host.covers(scope)
            if not inside:
                raise TokenRefused(f"{scope} is not in this host's namespaces: {', '.join(host.scopes)}")
        for folder in folders:
            if not _inside(folder, host.roots):
                raise TokenRefused(f"{folder} is not inside this host's folders")

    def revoke(self, token_id: str, *, host: TokenRecord | None = None) -> bool:
        """Stop accepting a token -- and, for a host token, every token it made. Answers whether there was one.

        Asked by a `host`, only a token that host made is revoked.
        """
        with file_lock(self._lock_path()):
            records = self.records()
            target = next((record for record in records if record.id == token_id), None)
            if target is None or (host is not None and target.host != host.id):
                return False
            self._write([record for record in records if token_id not in (record.id, record.host)])
        return True

    def made_by(self, host: TokenRecord) -> list[TokenRecord]:
        """The tokens a host made."""
        return [record for record in self.records() if record.host == host.id]

    def host_of(self, scope_id: str) -> TokenRecord | None:
        """The host whose namespace a scope is in, or None for a scope that is the Mac's own."""
        return next(
            (
                record
                for record in self.records()
                if record.kind == "host" and any(_covered(entry, scope_id) for entry in record.scopes)
            ),
            None,
        )

    def find(self, token_id: str) -> TokenRecord | None:
        return next((record for record in self.records() if record.id == token_id), None)

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
