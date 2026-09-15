# SPDX-License-Identifier: Apache-2.0
"""``sim-mirror mcp``: SimMirror's tools for one project, over stdio, backed by the local daemon.

1. **The daemon**: asked whether it is up (``/healthz``); when it is not, started detached and waited for, up to
   `HEALTH_WAIT_S`.
2. **A token**: the CLI runs as the person who installed SimMirror, so it reads the admin token and mints an agent token
   for this project's scope -- naming the folders its builds and installs may reach (``--root``). The token reaches the
   relay in its environment, never argv, and is revoked when the client goes.
3. **The relay** (`mcp.relay`), in this process, talking to ``/api/v1/agent``.
4. **A lease**: renewed every `RENEW_S` while the client runs, so the reaper leaves the project's device alone between
   tool calls.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import IO, Any

from sim_mirror.daemon.lease import RENEW_S
from sim_mirror.mcp import relay
from sim_mirror.scope import Scope

HEALTH_WAIT_S = 10.0
HEALTH_POLL_S = 0.2
REQUEST_TIMEOUT_S = 5.0
AGENT_PATH = "/api/v1/agent"
URL_ENV, TOKEN_ENV, SCOPE_ENV = "SIM_MIRROR_URL", "SIM_MIRROR_TOKEN", "SIM_MIRROR_SCOPE"
TOKEN_HEADER, SCOPE_HEADER, CLIENT_HEADER = "X-SimMirror-Token", "X-SimMirror-Scope", "X-SimMirror-Client"


class DaemonUnavailable(Exception):
    """The daemon could not be reached or started, said so a person knows what to do."""


class DaemonClient:
    """The local daemon, as the CLI talks to it."""

    def __init__(self, url: str, admin_token: str, *, opener: relay.Opener | None = None) -> None:
        self.url = url.rstrip("/")
        self._admin = admin_token
        self._open = opener or relay.direct_opener()

    def _request(self, method: str, path: str, payload: object = None, *, token: str | None = None) -> Any:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Authorization": f"Bearer {token or self._admin}", "Content-Type": "application/json"}
        request = urllib.request.Request(f"{self.url}{path}", data=data, method=method, headers=headers)
        with self._open(request, REQUEST_TIMEOUT_S) as response:
            answer = json.loads(response.read().decode("utf-8"))
        if not isinstance(answer, dict) or answer.get("ok") is not True:
            raise ValueError(f"the daemon answered {path} with something unexpected")
        return answer.get("data")

    def healthy(self) -> bool:
        try:
            self._request("GET", "/healthz")
        except (urllib.error.URLError, OSError, ValueError):
            return False
        return True

    def post(self, path: str, payload: object) -> Any:
        """An admin request. Raises `DaemonUnavailable` with what the daemon said."""
        try:
            return self._request("POST", path, payload)
        except urllib.error.HTTPError as exc:
            raise DaemonUnavailable(f"the daemon refused {path}: {relay.refusal(exc)}") from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise DaemonUnavailable(f"the daemon could not be reached: {exc}") from exc

    def mint_agent_token(self, scope_id: str, roots: Sequence[str], label: str) -> tuple[str, str]:
        """A new agent token for this scope: its id, and the token."""
        data = self.post(
            "/api/v1/admin/tokens", {"kind": "agent", "scopes": [scope_id], "roots": list(roots), "label": label}
        )
        return str(data["id"]), str(data["token"])

    def revoke(self, token_id: str) -> None:
        """Stop the daemon accepting a token; a daemon already gone has nothing to revoke."""
        try:
            self._request("DELETE", f"/api/v1/admin/tokens/{token_id}")
        except (urllib.error.URLError, OSError, ValueError):
            return

    def renew_lease(self, token: str, scope_id: str) -> bool:
        try:
            self._request("POST", f"{AGENT_PATH}/lease", {}, token=token)
        except (urllib.error.URLError, OSError, ValueError):
            return False
        return True


def ensure_daemon(
    client: DaemonClient,
    start: Callable[[], object],
    *,
    wait_s: float = HEALTH_WAIT_S,
    poll_s: float = HEALTH_POLL_S,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """The daemon, running: started when it is not, and waited for."""
    if client.healthy():
        return
    start()
    deadline = clock() + wait_s
    while not client.healthy():
        if clock() >= deadline:
            raise DaemonUnavailable(
                f"the SimMirror daemon did not start within {wait_s:g}s; run `sim-mirror serve` to see why"
            )
        sleep(poll_s)


def keep_leased(
    client: DaemonClient, token: str, scope_id: str, stop: threading.Event, every_s: float = RENEW_S
) -> None:
    """Renew the scope's lease now and every `every_s`, until `stop` is set."""
    while True:
        client.renew_lease(token, scope_id)
        if stop.wait(every_s):
            return


def relay_argv() -> list[str]:
    return [
        "--url-env",
        URL_ENV,
        "--header",
        f"{TOKEN_HEADER}={TOKEN_ENV}",
        "--header",
        f"{SCOPE_HEADER}={SCOPE_ENV}",
        "--client-header",
        CLIENT_HEADER,
    ]


def run(
    scope: Scope,
    roots: Sequence[Path],
    *,
    client: DaemonClient,
    start_daemon: Callable[[], object],
    env: Mapping[str, str] | None = None,
    stdin: IO[str] | None = None,
    stdout: IO[str] | None = None,
    every_s: float = RENEW_S,
    opener: relay.Opener | None = None,
) -> int:
    """Serve MCP for this scope until the client closes stdin."""
    ensure_daemon(client, start_daemon)
    label = f"sim-mirror mcp (pid {os.getpid()})"
    token_id, token = client.mint_agent_token(scope.id, [str(root) for root in roots], label)
    stop = threading.Event()
    keeper = threading.Thread(
        target=keep_leased, args=(client, token, scope.id, stop, every_s), name="sim-mirror-lease", daemon=True
    )
    keeper.start()
    relay_env = {
        **(os.environ if env is None else env),
        URL_ENV: client.url + AGENT_PATH,
        TOKEN_ENV: token,
        SCOPE_ENV: scope.id,
    }
    try:
        return relay.main(relay_argv(), env=relay_env, stdin=stdin, stdout=stdout, opener=opener)
    finally:
        stop.set()
        keeper.join(timeout=REQUEST_TIMEOUT_S)
        client.revoke(token_id)
