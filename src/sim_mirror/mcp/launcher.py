# SPDX-License-Identifier: Apache-2.0
"""``sim-mirror mcp``: SimMirror's tools for one project, over stdio, backed by the local daemon.

1. **The daemon**: asked whether it is up (``/healthz``) -- with a fresh nonce and no credential, and believed only when
   it proves it holds the admin token (`daemon.health`). When nothing answers, it is started detached and waited for,
   up to `HEALTH_WAIT_S`; when something else answers on its port, that is refused and sent nothing.
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

from sim_mirror.daemon import health
from sim_mirror.daemon.lease import RENEW_S
from sim_mirror.mcp import relay
from sim_mirror.scope import Scope

HEALTH_WAIT_S = 10.0
HEALTH_POLL_S = 0.2
REQUEST_TIMEOUT_S = 5.0
AGENT_PATH = "/api/v1/agent"
URL_ENV, TOKEN_ENV, SCOPE_ENV = "SIM_MIRROR_URL", "SIM_MIRROR_TOKEN", "SIM_MIRROR_SCOPE"
TOKEN_HEADER, SCOPE_HEADER, CLIENT_HEADER = "X-SimMirror-Token", "X-SimMirror-Scope", "X-SimMirror-Client"
#: What a probe of the daemon's port found: this user's daemon, something else, or nothing listening.
OURS, OTHER, NOTHING = "ours", "other", "nothing"


class DaemonUnavailable(Exception):
    """The daemon could not be reached or started, said so a person knows what to do."""


class NotTheDaemon(ValueError):
    """Something answered on the daemon's port without proving it is this user's daemon."""


def impostor(url: str) -> str:
    return (
        f"something on {url} answered that is not your SimMirror daemon, so nothing was sent to it: stop it, or move "
        "SimMirror to another port with `sim-mirror config set server.port <port>`"
    )


class DaemonClient:
    """The local daemon, as the CLI talks to it: a credential goes only to a listener that proved it is the daemon."""

    def __init__(self, url: str, admin_token: str, *, opener: relay.Opener | None = None) -> None:
        self.url = url.rstrip("/")
        self._admin = admin_token
        self._open = opener or relay.direct_opener()
        self._ours = False

    def _send(self, method: str, path: str, payload: object, headers: Mapping[str, str]) -> Any:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.url}{path}", data=data, method=method, headers={"Content-Type": "application/json", **headers}
        )
        with self._open(request, REQUEST_TIMEOUT_S) as response:
            answer = json.loads(response.read().decode("utf-8"))
        if not isinstance(answer, dict) or answer.get("ok") is not True:
            raise ValueError(f"the daemon answered {path} with something unexpected")
        return answer.get("data")

    def probe(self) -> str:
        """`OURS`, `OTHER` or `NOTHING`: a fresh nonce and no credential, so a listener that is not the daemon learns
        nothing it could use."""
        nonce = health.new_nonce()
        try:
            data = self._send("GET", f"/healthz?nonce={nonce}", None, {})
        except urllib.error.HTTPError:
            self._ours = False
            return OTHER
        except (urllib.error.URLError, OSError):
            self._ours = False
            return NOTHING
        except ValueError:
            self._ours = False
            return OTHER
        self._ours = isinstance(data, dict) and health.proves(self._admin, nonce, data.get("proof"))
        return OURS if self._ours else OTHER

    def healthy(self) -> bool:
        return self.probe() == OURS

    def _request(self, method: str, path: str, payload: object = None, *, token: str | None = None) -> Any:
        """A request with a credential, sent only once the listener has proved it is this user's daemon."""
        if not self._ours:
            found = self.probe()
            if found == NOTHING:
                raise urllib.error.URLError(f"nothing answers at {self.url}")
            if found == OTHER:
                raise NotTheDaemon(impostor(self.url))
        try:
            return self._send(method, path, payload, {"Authorization": f"Bearer {token or self._admin}"})
        except (urllib.error.URLError, OSError):
            # The daemon may have gone; whatever answers on the port next must prove itself again.
            self._ours = False
            raise

    def admin(self, method: str, path: str, payload: object = None) -> Any:
        """A request with the admin token, answering its data. Raises `DaemonUnavailable` with what the daemon said."""
        try:
            return self._request(method, path, payload)
        except NotTheDaemon as exc:
            raise DaemonUnavailable(str(exc)) from exc
        except urllib.error.HTTPError as exc:
            raise DaemonUnavailable(f"the daemon refused {path}: {relay.refusal(exc)}") from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise DaemonUnavailable(f"the daemon could not be reached: {exc}") from exc

    def get(self, path: str) -> Any:
        return self.admin("GET", path)

    def post(self, path: str, payload: object) -> Any:
        return self.admin("POST", path, payload)

    def put(self, path: str, payload: object) -> Any:
        return self.admin("PUT", path, payload)

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
    """This user's daemon, running: started when nothing answers, and waited for. Something else on its port is
    refused rather than started over or trusted."""
    found = client.probe()
    if found == OURS:
        return
    if found == OTHER:
        raise DaemonUnavailable(impostor(client.url))
    start()
    deadline = clock() + wait_s
    while (found := client.probe()) != OURS:
        if found == OTHER:
            raise DaemonUnavailable(impostor(client.url))
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
