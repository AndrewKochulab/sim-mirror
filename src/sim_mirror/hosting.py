# SPDX-License-Identifier: Apache-2.0
"""A host application sharing the local daemon with others, instead of running a SimMirror of its own.

The person who installed SimMirror makes the host a token for its namespaces::

    sim-mirror token create --kind host --scope 'notes:*' --root ~/Projects --label "Notes app"

and the host's backend uses it through `DaemonHost` -- only for scopes in those namespaces, such as ``notes:42``. It
brings their devices up and lets them go, frames their screens with embed tickets, changes their settings, and makes
agent and viewer tokens for them, whose folders are inside its own. What another host does, and the devices another
host's scopes run, it neither sees nor reaches.

A credential goes only to a listener that proved it is the daemon: before its first request the host sends
``/healthz`` a fresh nonce and its token's id, and believes the answer only when it carries a proof keyed by its token's
digest, which the daemon keeps and nothing else on the port has (`daemon.health`).

It speaks to the daemon over the standard library's HTTP, as the command line does, and gives an agent the same relay.
"""

from __future__ import annotations

import urllib.error
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from sim_mirror.mcp import relay
from sim_mirror.mcp.launcher import (
    AGENT_PATH,
    CLIENT_HEADER,
    SCOPE_ENV,
    SCOPE_HEADER,
    TOKEN_ENV,
    TOKEN_HEADER,
    URL_ENV,
    DaemonClient,
    DaemonUnavailable,
    NotTheDaemon,
)

SCOPES_PATH = "/api/v1/scopes"
HOST_PATH = "/api/v1/host"


def unproven(url: str) -> str:
    return (
        f"what answers on {url} did not prove it knows this host token, so nothing was sent to it: the token was "
        "revoked or does not match its id, or it is not your SimMirror daemon"
    )


class DaemonRefused(Exception):
    """The daemon refused a host's request: its HTTP status, and what it said."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class AgentAccess:
    """What an agent a host starts needs: its MCP server's command line, and the environment to run it with.

    The token is in `env` only, never `argv`. `token_id` revokes it when the agent is done.
    """

    token_id: str
    argv: list[str]
    env: dict[str, str]


class DaemonHost:
    """One host's view of the local daemon, through its host token."""

    def __init__(self, url: str, token: str, token_id: str, *, opener: relay.Opener | None = None) -> None:
        if not relay.is_loopback(url):
            raise ValueError("the daemon is reached on this Mac only: its URL is http://127.0.0.1:<port>")
        self._client = DaemonClient(url, token, token_id=token_id, opener=opener)
        self.url = self._client.url

    def _call(self, method: str, path: str, payload: object = None, *, token: str | None = None) -> Any:
        try:
            return self._client.request(method, path, payload, token=token)
        except NotTheDaemon:
            raise DaemonUnavailable(unproven(self.url)) from None
        except urllib.error.HTTPError as exc:
            raise DaemonRefused(exc.code, relay.refusal(exc)) from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise DaemonUnavailable(f"the daemon could not be reached: {exc}") from exc

    @staticmethod
    def _scope(scope_id: str) -> str:
        return f"{SCOPES_PATH}/{quote(scope_id, safe='')}"

    # -- the host itself -------------------------------------------------------------------------------------------

    def record(self) -> dict[str, Any]:
        """This host's token record: its namespaces, folders and label."""
        return dict(self._call("GET", HOST_PATH))

    # -- a scope's simulator ---------------------------------------------------------------------------------------

    def status(self, scope_id: str) -> dict[str, Any]:
        """Whether the scope can have a simulator, why not, and how its device stands."""
        return dict(self._call("GET", self._scope(scope_id)))

    def start(self, scope_id: str) -> dict[str, Any]:
        """Bring the scope's device up; the answer is its status with a ticket for its screen socket."""
        return dict(self._call("POST", self._scope(scope_id), {}))

    def stop(self, scope_id: str, *, shutdown: bool = False) -> bool:
        """Let the scope's device go -- shutting it down too, with `shutdown`. Answers whether one was running."""
        path = self._scope(scope_id) + ("?shutdown=true" if shutdown else "")
        return bool(self._call("DELETE", path)["stopped"])

    def devices(self, scope_id: str) -> list[dict[str, Any]]:
        """The simulators this scope could use: this Mac's, less those another host's scopes are running."""
        return list(self._call("GET", self._scope(scope_id) + "/devices")["devices"])

    def choose(self, scope_id: str, udid: str) -> None:
        """Use this simulator for the scope from now on."""
        self._call("PUT", self._scope(scope_id) + "/device", {"udid": udid})

    def embed_url(self, scope_id: str) -> str:
        """The URL a page frames to show the scope's screen, with a one-shot ticket in its fragment."""
        return self.url + str(self._call("POST", self._scope(scope_id) + "/embed-tickets", {})["url"])

    # -- a scope's settings ----------------------------------------------------------------------------------------

    def settings(self, scope_id: str) -> dict[str, Any]:
        """Every setting as the scope sees it, where each value comes from, and what this host may change."""
        return dict(self._call("GET", self._scope(scope_id) + "/settings"))

    def change_settings(
        self, scope_id: str, values: Mapping[str, Any] | None = None, unset: Sequence[str] = ()
    ) -> dict[str, Any]:
        """Set values and put settings back for this scope, all or none. A sensitive one waits for a person at the
        terminal, and is refused with 428 until then."""
        change = {
            "target": "scope",
            "set": [{"path": path, "value": value} for path, value in (values or {}).items()],
            "unset": list(unset),
        }
        return dict(self._call("PATCH", self._scope(scope_id) + "/settings", change))

    # -- tokens ----------------------------------------------------------------------------------------------------

    def create_token(
        self, kind: str, scopes: Sequence[str], *, label: str = "", roots: Sequence[str] = ()
    ) -> dict[str, Any]:
        """An agent or viewer token for scopes in this host's namespaces. The answer's ``token`` is shown only once."""
        payload = {"kind": kind, "scopes": list(scopes), "label": label, "roots": list(roots)}
        return dict(self._call("POST", HOST_PATH + "/tokens", payload))

    def tokens(self) -> list[dict[str, Any]]:
        """The tokens this host made."""
        return list(self._call("GET", HOST_PATH + "/tokens")["tokens"])

    def revoke(self, token_id: str) -> bool:
        """Revoke a token this host made. Answers whether there was one."""
        return bool(self._call("DELETE", f"{HOST_PATH}/tokens/{quote(token_id, safe='')}")["revoked"])

    def agent(
        self,
        scope_id: str,
        *,
        label: str = "",
        roots: Sequence[str] = (),
        python: str | None = None,
        server_name: str = relay.DEFAULT_SERVER_NAME,
    ) -> AgentAccess:
        """An agent token for one scope, as the MCP server command an agent's client runs and its environment."""
        made = self.create_token("agent", [scope_id], label=label, roots=roots)
        headers = {TOKEN_HEADER: TOKEN_ENV, SCOPE_HEADER: SCOPE_ENV}
        extra = {} if python is None else {"python": python}
        argv = relay.relay_command(
            url_env=URL_ENV, headers=headers, client_header=CLIENT_HEADER, server_name=server_name, **extra
        )
        env = {URL_ENV: self.url + AGENT_PATH, TOKEN_ENV: str(made["token"]), SCOPE_ENV: scope_id}
        return AgentAccess(str(made["id"]), argv, env)

    def keep(self, access: AgentAccess) -> bool:
        """Renew the lease that keeps an agent's device from being stopped as idle between its calls; renew it more
        often than every 90 seconds while the agent runs. Answers whether the daemon took it."""
        try:
            self._call("POST", AGENT_PATH + "/lease", {}, token=access.env[TOKEN_ENV])
        except (DaemonRefused, DaemonUnavailable):
            return False
        return True
