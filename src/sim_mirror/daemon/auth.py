# SPDX-License-Identifier: Apache-2.0
"""Who is asking the daemon: the standalone `Authenticator`, over its tokens.

A token comes as ``Authorization: Bearer …`` or ``X-SimMirror-Token``. A person's routes take the admin token, a viewer
token for the scope, or a page's viewer session for it; an agent's routes take only an agent token, for the scope it
names in ``X-SimMirror-Scope`` (or its only scope), and its title -- what viewers show beside its cursor -- from
``X-SimMirror-Client``. A screen socket's credential is its ticket, so letting one in only checks its scope: the Host
and the Origin were already checked (`server.security`).

Settings are read and changed only from the daemon's own pages: a request carrying any other Origin -- even one
``security.allowed_origins`` lets call the API -- or a browser's cross-site ``Sec-Fetch-Site`` is refused, since an
allowed origin gets CORS answers on every route. A ``settings`` session (``sim-mirror open --settings``) may change
settings but not the sensitive ones alone; a viewer session or viewer token may read them; an embed frame may not see
them; and the admin token from outside any page -- the command line -- may change everything.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from fastapi import Request, WebSocket

from sim_mirror.daemon.passes import ViewerSessions
from sim_mirror.daemon.tokens import ALL_SCOPES, TokenRecord, TokenStore
from sim_mirror.protocol import CLOSE_FORBIDDEN
from sim_mirror.scope import InvalidScope, Scope
from sim_mirror.seams import Admission, Caller, Person, Refused, SettingsEditor
from sim_mirror.server.security import origin_of

#: The spelling `sim-mirror mcp` gives its relay (`mcp.launcher`); header names are compared case-insensitively.
TOKEN_HEADER = "x-simmirror-token"
SCOPE_HEADER = "x-simmirror-scope"
CLIENT_HEADER = "x-simmirror-client"
TITLE_MAX = 80
DEFAULT_TITLE = "agent"

AUTHENTICATION_REQUIRED = "authentication required"
NOT_FOR_SCOPE = "this token is not for that scope"
NOT_AN_AGENT = "this token is not an agent's"
ADMIN_ONLY = "this needs the admin token"
NAME_THE_SCOPE = "this token is for several scopes; name one in X-SimMirror-Scope"
NO_SUCH_SCOPE = "there is no such scope"
SETTINGS_OWN_PAGES = "settings are read and changed only from SimMirror's own pages"
SETTINGS_NOT_IN_A_FRAME = "a framed viewer cannot see settings; open them with `sim-mirror open --settings`"
#: What a browser says of a request from another site; `same-origin` and `none` (typed or bookmarked) are its own.
CROSS_SITE = frozenset({"cross-site", "same-site"})


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
    def __init__(
        self,
        tokens: TokenStore,
        viewers: ViewerSessions,
        own_origins: Callable[[], frozenset[str]] = frozenset,
    ) -> None:
        self._tokens = tokens
        self._viewers = viewers
        self._own_origins = own_origins

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

    async def settings_editor(self, request: Request, scope_id: str) -> SettingsEditor:
        scope = scope_named(scope_id)
        origin = request.headers.get("origin")
        if origin is not None and origin_of(origin) not in self._own_origins():
            raise Refused(403, SETTINGS_OWN_PAGES)
        if request.headers.get("sec-fetch-site", "").lower() in CROSS_SITE:
            raise Refused(403, SETTINGS_OWN_PAGES)
        token, record = self._record(request.headers)
        session = self._viewers.session(token)
        if session is not None and session.scope_id == scope.id:
            if session.kind == "embed":
                raise Refused(403, SETTINGS_NOT_IN_A_FRAME)
            return SettingsEditor(scope, may_write=session.kind == "settings")
        if record is None and session is None:
            raise Refused(401, AUTHENTICATION_REQUIRED)
        if record is None or record.kind == "agent" or not record.covers(scope.id):
            raise Refused(403, NOT_FOR_SCOPE)
        admin = record.kind == "admin"
        return SettingsEditor(scope, may_write=admin, may_write_sensitive=admin and origin is None)

    async def admit_socket(self, websocket: WebSocket, scope_id: str) -> Admission:
        return Admission(scope_named(scope_id, status=CLOSE_FORBIDDEN))
