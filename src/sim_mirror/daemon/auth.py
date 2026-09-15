# SPDX-License-Identifier: Apache-2.0
"""Who is asking the daemon: the standalone `Authenticator`, over its tokens.

A token comes as ``Authorization: Bearer …`` or ``X-SimMirror-Token``. A person's routes take the admin token, a viewer
token for the scope, or a page's viewer session for it; an agent's routes take only an agent token, for the scope it
names in ``X-SimMirror-Scope`` (or its only scope), and its title -- what viewers show beside its cursor -- from
``X-SimMirror-Client``. A screen socket's credential is its ticket, so letting one in only checks its scope: the Host
and the Origin were already checked (`server.security`).
"""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import Request, WebSocket

from sim_mirror.daemon.passes import ViewerSessions
from sim_mirror.daemon.tokens import ALL_SCOPES, TokenRecord, TokenStore
from sim_mirror.protocol import CLOSE_FORBIDDEN
from sim_mirror.scope import InvalidScope, Scope
from sim_mirror.seams import Admission, Caller, Person, Refused

TOKEN_HEADER = "x-sim-mirror-token"
SCOPE_HEADER = "x-sim-mirror-scope"
CLIENT_HEADER = "x-sim-mirror-client"
TITLE_MAX = 80
DEFAULT_TITLE = "agent"

AUTHENTICATION_REQUIRED = "authentication required"
NOT_FOR_SCOPE = "this token is not for that scope"
NOT_AN_AGENT = "this token is not an agent's"
ADMIN_ONLY = "this needs the admin token"
NAME_THE_SCOPE = "this token is for several scopes; name one in X-SimMirror-Scope"
NO_SUCH_SCOPE = "there is no such scope"


def presented_token(headers: Mapping[str, str]) -> str | None:
    authorization = headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None
    return headers.get(TOKEN_HEADER) or None


def scope_named(scope_id: str, *, status: int = 404) -> Scope:
    try:
        return Scope.named(scope_id)
    except InvalidScope as exc:
        raise Refused(status, NO_SUCH_SCOPE) from exc


class TokenAuthenticator:
    def __init__(self, tokens: TokenStore, viewers: ViewerSessions) -> None:
        self._tokens = tokens
        self._viewers = viewers

    def _record(self, headers: Mapping[str, str]) -> tuple[str, TokenRecord | None]:
        token = presented_token(headers)
        if token is None:
            raise Refused(401, AUTHENTICATION_REQUIRED)
        return token, self._tokens.match(token)

    def admin(self, request: Request) -> TokenRecord:
        """The admin credential a request carries. Raises `Refused`."""
        token, record = self._record(request.headers)
        if record is None and self._viewers.scope_of(token) is None:
            raise Refused(401, AUTHENTICATION_REQUIRED)
        if record is None or record.kind != "admin":
            raise Refused(403, ADMIN_ONLY)
        return record

    async def person(self, request: Request, scope_id: str) -> Person:
        scope = scope_named(scope_id)
        token, record = self._record(request.headers)
        session = self._viewers.scope_of(token)
        if session == scope.id:
            return Person(scope)
        if record is None and session is None:
            raise Refused(401, AUTHENTICATION_REQUIRED)
        if record is None or record.kind == "agent" or not record.covers(scope.id):
            raise Refused(403, NOT_FOR_SCOPE)
        return Person(scope)

    async def agent(self, request: Request) -> Caller:
        _, record = self._record(request.headers)
        if record is None:
            raise Refused(401, AUTHENTICATION_REQUIRED)
        if record.kind != "agent":
            raise Refused(403, NOT_AN_AGENT)
        named = request.headers.get(SCOPE_HEADER, "")
        if not named:
            if len(record.scopes) != 1 or record.scopes[0] == ALL_SCOPES:
                raise Refused(400, NAME_THE_SCOPE)
            named = record.scopes[0]
        scope = scope_named(named)
        if not record.covers(scope.id):
            raise Refused(403, NOT_FOR_SCOPE)
        title = " ".join(request.headers.get(CLIENT_HEADER, "").split())[:TITLE_MAX] or DEFAULT_TITLE
        return Caller(scope, key=record.id, title=title)

    async def admit_socket(self, websocket: WebSocket, scope_id: str) -> Admission:
        return Admission(scope_named(scope_id, status=CLOSE_FORBIDDEN))
