# SPDX-License-Identifier: Apache-2.0
"""``sim-mirror mcp``: SimMirror's tools for one project, over stdio, backed by the local daemon.

1. **The daemon**: asked whether it is up (``/healthz``) -- with a fresh nonce and no credential, and believed only when
   it proves it holds the admin token (`daemon.health`). When nothing answers, it is started detached and waited for,
   up to `HEALTH_WAIT_S`; when something else answers on its port, that is refused and sent nothing. A daemon of an
   older SimMirror -- still running after the plugin was updated -- is stopped and this version's started in its place,
   so the tools the client is offered are this version's; its viewers and other clients reconnect by themselves.
2. **A token**: the CLI runs as the person who installed SimMirror, so it reads the admin token and mints an agent token
   for this project's scope -- naming the folders its builds and installs may reach (``--root``). The token reaches the
   relay in its environment, never argv, and is revoked when the client goes.
3. **The relay** (`mcp.relay`), in this process, talking to ``/api/v1/agent``. It passes on the folders the client
   says it works in (its MCP roots), so a build runs in the one the agent is in -- the same project's git worktree,
   say -- when its token may build there (`build.checkouts`).
4. **A lease** (`LeaseKeeper`): taken at the client's first tool call and renewed every `RENEW_S` while calls keep
   coming, so the reaper leaves the project's device alone between them. A client that has made none for
   ``device.agent_idle_minutes`` -- which each renewal's answer says, from the scope's settings in the daemon -- stops
   renewing, so an idle session does not keep a device, and the WebDriverAgent on a phone, up for ever; its next call
   takes the lease again. A token the daemon refuses -- revoked -- is not renewed again, and revoking it when the
   client goes lets the lease go at once.
5. **The daemon back** (`reviver`): a call that finds nothing listening -- the daemon stopped, or gone -- starts it
   again as step 1 does and is sent once more, its token still good. After ``sim-mirror stop --daemon`` the first such
   call only answers that a person stopped it, and the next starts it. Two relays starting a daemon at once start one:
   each start waits for the run folder's start lock. A daemon started again may listen on another port -- its config
   changed -- so where it listens is read again each time it is looked for (`daemon.json`, else the config), and the
   relay's calls follow it there once what answers proved it is this user's daemon (`follower`).
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import IO, Any
from urllib.parse import quote

from sim_mirror._version import __version__
from sim_mirror.daemon import health, lifecycle
from sim_mirror.daemon.lease import RENEW_S
from sim_mirror.mcp import relay
from sim_mirror.scope import Scope
from sim_mirror.storage.private import file_lock

HEALTH_WAIT_S = 10.0
HEALTH_POLL_S = 0.2
REQUEST_TIMEOUT_S = 5.0
AGENT_PATH = "/api/v1/agent"
URL_ENV, TOKEN_ENV, SCOPE_ENV = "SIM_MIRROR_URL", "SIM_MIRROR_TOKEN", "SIM_MIRROR_SCOPE"
TOKEN_HEADER, SCOPE_HEADER, CLIENT_HEADER = "X-SimMirror-Token", "X-SimMirror-Scope", "X-SimMirror-Client"
ROOTS_HEADER = "X-SimMirror-Roots"
#: What a probe of the daemon's port found: this user's daemon, something else, or nothing listening.
OURS, OTHER, NOTHING = "ours", "other", "nothing"
#: What the lease route answers a token it will not take: unknown or revoked, or not an agent's or this scope's.
REFUSED_TOKEN = (401, 403)
#: How long a client may go without a tool call before its lease is no longer renewed, until a renewal's answer says
#: what the scope's ``device.agent_idle_minutes`` is.
AGENT_IDLE_S = 30 * 60.0
STOPPED_ON_PURPOSE = (
    "SimMirror was stopped by `sim-mirror stop --daemon`, so this call was not made. Call again to start it, or "
    "reconnect this MCP server."
)


def version_of(server: object) -> tuple[int, ...] | None:
    """A SimMirror version from what a daemon calls itself -- ``sim-mirror/2.0.0`` -- or None."""
    if not isinstance(server, str) or not server.startswith("sim-mirror/"):
        return None
    try:
        return tuple(int(part) for part in server.removeprefix("sim-mirror/").split("+")[0].split("."))
    except ValueError:
        return None


def older(server: object, mine: str = __version__) -> bool:
    """Whether a daemon calling itself `server` runs an older SimMirror than this one. One that says no version is
    not called older: it is left as it is."""
    theirs, ours = version_of(server), version_of(f"sim-mirror/{mine}")
    return theirs is not None and ours is not None and theirs < ours


class DaemonUnavailable(Exception):
    """The daemon could not be reached or started, said so a person knows what to do."""


class NotTheDaemon(ValueError):
    """Something answered on the daemon's port without proving it is this user's daemon."""


class LeaseRefused(Exception):
    """The daemon refused an agent token's lease for the token itself -- revoked, or not an agent's."""


def impostor(url: str) -> str:
    return (
        f"something on {url} answered that is not your SimMirror daemon, so nothing was sent to it: stop it, or move "
        "SimMirror to another port with `sim-mirror config set server.port <port>`"
    )


class DaemonClient:
    """The local daemon, as the CLI talks to it: a credential goes only to a listener that proved it is the daemon.

    The CLI holds the admin token, and the daemon proves it holds that too. A host holds only a scoped token, so it
    names the token's id (`token_id`) and the daemon proves it knows that token's digest instead.
    """

    def __init__(
        self, url: str, token: str, *, token_id: str | None = None, opener: relay.Opener | None = None
    ) -> None:
        self.url = url.rstrip("/")
        self._token = token
        self._token_id = token_id
        self._open = opener or relay.direct_opener()
        #: The URL whose listener last proved it is this user's daemon; a credential goes nowhere else.
        self._proven: str | None = None
        #: What the daemon called itself when it last proved it is this user's: ``sim-mirror/<version>``.
        self.server: str | None = None

    def move(self, url: str) -> None:
        """Look for the daemon at `url` from now on -- one started again may listen on another port. What answers there
        proves itself before it is sent a credential."""
        self.url = url.rstrip("/")

    def _send(
        self,
        method: str,
        path: str,
        payload: object,
        headers: Mapping[str, str],
        url: str,
        timeout: float = REQUEST_TIMEOUT_S,
    ) -> Any:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{url}{path}", data=data, method=method, headers={"Content-Type": "application/json", **headers}
        )
        with self._open(request, timeout) as response:
            answer = json.loads(response.read().decode("utf-8"))
        if not isinstance(answer, dict) or answer.get("ok") is not True:
            raise ValueError(f"the daemon answered {path} with something unexpected")
        return answer.get("data")

    def probe(self) -> str:
        """`OURS`, `OTHER` or `NOTHING`: a fresh nonce and no credential, so a listener that is not the daemon learns
        nothing it could use."""
        return self._probe(self.url)

    def _probe(self, url: str) -> str:
        nonce = health.new_nonce()
        asked = f"/healthz?nonce={nonce}" + (f"&token_id={quote(self._token_id, safe='')}" if self._token_id else "")
        found = OTHER
        try:
            data = self._send("GET", asked, None, {}, url)
        except (urllib.error.HTTPError, ValueError):
            data = None  # something answered, and not as the daemon does
        except (urllib.error.URLError, OSError):
            data, found = None, NOTHING
        if isinstance(data, dict):
            if self._token_id is None:
                ours = health.proves(self._token, nonce, data.get("proof"))
            else:
                ours = health.token_proves(self._token, nonce, data.get("token_proof"))
            if ours:
                server = data.get("server")
                self.server = server if isinstance(server, str) else None
                found = OURS
        self._proven = url if found == OURS else None
        return found

    def healthy(self) -> bool:
        return self.probe() == OURS

    def request(
        self,
        method: str,
        path: str,
        payload: object = None,
        *,
        token: str | None = None,
        timeout: float = REQUEST_TIMEOUT_S,
    ) -> Any:
        """A request with a credential, sent only to a listener that has proved it is this user's daemon -- answered
        within `timeout` seconds."""
        url = self.url
        if self._proven != url:
            found = self._probe(url)
            if found == NOTHING:
                raise urllib.error.URLError(f"nothing answers at {url}")
            if found == OTHER:
                raise NotTheDaemon(impostor(url))
        try:
            return self._send(method, path, payload, {"Authorization": f"Bearer {token or self._token}"}, url, timeout)
        except (urllib.error.URLError, OSError):
            # The daemon may have gone; whatever answers on the port next must prove itself again.
            self._proven = None
            raise

    def admin(self, method: str, path: str, payload: object = None, *, timeout: float = REQUEST_TIMEOUT_S) -> Any:
        """A request with the admin token, answering its data. Raises `DaemonUnavailable` with what the daemon said."""
        try:
            return self.request(method, path, payload, timeout=timeout)
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
            self.request("DELETE", f"/api/v1/admin/tokens/{token_id}")
        except (urllib.error.URLError, OSError, ValueError):
            return

    def lease(self, token: str) -> Mapping[str, Any] | None:
        """Renew an agent token's lease on its scope, answering what the daemon said -- or None when it could not be
        reached. Raises `LeaseRefused` when the daemon refused the token itself: renewing it again is no use."""
        try:
            answer = self.request("POST", f"{AGENT_PATH}/lease", {}, token=token)
        except urllib.error.HTTPError as exc:
            if exc.code in REFUSED_TOKEN:
                raise LeaseRefused(relay.refusal(exc)) from exc
            return None
        except (urllib.error.URLError, OSError, ValueError):
            return None
        return answer if isinstance(answer, dict) else {}

    def renew_lease(self, token: str, scope_id: str) -> bool:
        """Whether the lease was renewed."""
        try:
            return self.lease(token) is not None
        except LeaseRefused:
            return False


def ensure_daemon(
    client: DaemonClient,
    start: Callable[[], object],
    *,
    wait_s: float = HEALTH_WAIT_S,
    poll_s: float = HEALTH_POLL_S,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    retire: Callable[[], object] | None = None,
    locate: Callable[[], str] | None = None,
) -> None:
    """This user's daemon, running: started when nothing answers, and waited for. Something else on its port is
    refused rather than started over or trusted. With `retire`, a daemon of an older SimMirror is stopped by it and
    this version's started instead; a newer one is used as it is. With `locate`, the daemon is looked for each time
    where it says the daemon listens now: one started again may have read another port."""

    def look() -> str:
        if locate is not None:
            client.move(locate())
        return client.probe()

    found = look()
    if found == OURS and retire is not None and older(client.server):
        retire()
        deadline = clock() + wait_s
        while (found := look()) == OURS:
            if clock() >= deadline:
                raise DaemonUnavailable(
                    f"the older SimMirror daemon ({client.server}) did not stop within {wait_s:g}s; stop it, and "
                    "this version starts in its place"
                )
            sleep(poll_s)
    if found == OURS:
        return
    if found == OTHER:
        raise DaemonUnavailable(impostor(client.url))
    start()
    deadline = clock() + wait_s
    while (found := look()) != OURS:
        if found == OTHER:
            raise DaemonUnavailable(impostor(client.url))
        if clock() >= deadline:
            raise DaemonUnavailable(
                f"the SimMirror daemon did not start within {wait_s:g}s; run `sim-mirror serve` to see why"
            )
        sleep(poll_s)


def starting(run_dir: Path | None) -> contextlib.AbstractContextManager[None]:
    """The run folder's start lock, held while a daemon is found or started; nothing without a run folder."""
    return contextlib.nullcontext() if run_dir is None else file_lock(run_dir / lifecycle.START_LOCK_FILE)


def reviver(
    client: DaemonClient,
    start: Callable[[], object],
    run_dir: Path | None = None,
    locate: Callable[[], str] | None = None,
) -> relay.Revive:
    """What the relay asks when a call finds nothing listening: the daemon started again, as on first launch, and None
    -- or why not. A daemon a person stopped (`lifecycle.mark_stopped`) is said to be stopped, once for each stop,
    before a call starts it again. With `locate`, the daemon is looked for where it listens now (`ensure_daemon`), so
    a relay that waited for another's start finds the daemon that one started, on whatever port it listens."""
    told: set[str] = set()

    def revive() -> str | None:
        mark = lifecycle.stopped_mark(run_dir) if run_dir is not None else None
        if mark is not None and mark not in told:
            told.add(mark)
            return STOPPED_ON_PURPOSE
        try:
            with starting(run_dir):
                ensure_daemon(client, start, locate=locate)
        except DaemonUnavailable as exc:
            return f"The SimMirror daemon is not running, and could not be started again: {exc}"
        return None

    return revive


def follower(client: DaemonClient, locate: Callable[[], str]) -> relay.Locate:
    """Where the relay's calls go now: the agent routes of the daemon where `locate` says it listens, once what answers
    there proved it is this user's daemon -- else None, and calls stay where they went."""

    def where() -> str | None:
        client.move(locate())
        return client.url + AGENT_PATH if client.healthy() else None

    return where


class LeaseKeeper:
    """The scope's lease, held while the client is at work: taken at its first tool call, renewed every `every_s` while
    calls come within the scope's ``device.agent_idle_minutes`` of the last, and left to lapse after. `run` is the
    thread that renews it; the relay says when a call is handed on (`called`)."""

    def __init__(
        self,
        client: DaemonClient,
        token: str,
        *,
        every_s: float = RENEW_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._token = token
        self._every_s = every_s
        self._clock = clock
        #: How long after a call the lease is still renewed: the scope's setting, once a renewal has said it.
        self.idle_s = AGENT_IDLE_S
        self._last_call: float | None = None
        #: When it is renewed next while calls come; at once the first time.
        self._due = 0.0
        self._stopped = False
        self._wake = threading.Condition()

    def called(self) -> None:
        """A tool call was handed on: the lease is held from now -- taken at once when it was not."""
        with self._wake:
            self._last_call = self._clock()
            self._wake.notify_all()

    def stop(self) -> None:
        with self._wake:
            self._stopped = True
            self._wake.notify_all()

    def wait_s(self) -> float | None:
        """How long until the lease is renewed next: 0 for now, or None when it is not until another call comes --
        none yet, or none for `idle_s`."""
        now = self._clock()
        if self._last_call is None or now - self._last_call >= self.idle_s:
            return None
        return max(0.0, self._due - now)

    def renew(self) -> bool:
        """Renew it now, learning the scope's idle window from the answer. False once the daemon refused the token --
        revoked -- so it is not renewed again; a daemon that could not be reached is asked again next time."""
        try:
            answer = self._client.lease(self._token)
        except LeaseRefused:
            return False
        idle_s = answer.get("agent_idle_s") if answer is not None else None
        if isinstance(idle_s, (int, float)) and not isinstance(idle_s, bool) and idle_s > 0:
            self.idle_s = float(idle_s)
        self._due = self._clock() + self._every_s
        return True

    def run(self) -> None:
        """Hold the lease as calls come, until `stop` -- or until the daemon refuses the token."""
        while True:
            with self._wake:
                while not self._stopped and (wait := self.wait_s()) != 0:
                    self._wake.wait(wait)
                if self._stopped:
                    return
            if not self.renew():
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
        "--roots-header",
        ROOTS_HEADER,
        "--server-version",
        __version__,
    ]


def run(
    scope: Scope,
    roots: Sequence[Path],
    *,
    client: DaemonClient,
    start_daemon: Callable[[], object],
    retire_daemon: Callable[[], object] | None = None,
    env: Mapping[str, str] | None = None,
    stdin: IO[str] | None = None,
    stdout: IO[str] | None = None,
    every_s: float = RENEW_S,
    opener: relay.Opener | None = None,
    run_dir: Path | None = None,
    locate_daemon: Callable[[], str] | None = None,
) -> int:
    """Serve MCP for this scope until the client closes stdin. With `run_dir`, a daemon is started under its start
    lock, and one a person stopped is said to be (`reviver`). With `locate_daemon` -- the daemon's URL as its run
    folder and config say now -- the daemon is followed to another port when it is started again on one."""
    with starting(run_dir):
        ensure_daemon(client, start_daemon, retire=retire_daemon, locate=locate_daemon)
    label = f"sim-mirror mcp (pid {os.getpid()})"
    token_id, token = client.mint_agent_token(scope.id, [str(root) for root in roots], label)
    lease = LeaseKeeper(client, token, every_s=every_s)
    keeper = threading.Thread(target=lease.run, name="sim-mirror-lease", daemon=True)
    keeper.start()
    relay_env = {
        **(os.environ if env is None else env),
        URL_ENV: client.url + AGENT_PATH,
        TOKEN_ENV: token,
        SCOPE_ENV: scope.id,
    }
    try:
        revive = reviver(client, start_daemon, run_dir, locate_daemon)
        locate = follower(client, locate_daemon) if locate_daemon is not None else None
        return relay.main(
            relay_argv(),
            env=relay_env,
            stdin=stdin,
            stdout=stdout,
            opener=opener,
            revive=revive,
            locate=locate,
            on_call=lease.called,
        )
    finally:
        lease.stop()
        keeper.join(timeout=REQUEST_TIMEOUT_S)
        # Revoking the token lets go of its lease too, so the device can idle out without waiting for it to lapse.
        client.revoke(token_id)
