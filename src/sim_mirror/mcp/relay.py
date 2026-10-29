#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""An MCP server over stdio that knows no tools: it asks a SimMirror server which there are, and hands it every call.

An MCP client -- Claude Code, Codex, Cursor -- starts this over stdio. It lists the tools the server offers now -- asked
again each time, since they change with settings and with what a device's connector can do, and the client is told
when they no longer match what it was given -- and hands every call to the server, which does the work. So the tools,
their checks and what a viewer shows live in one place, and all a client can reach is this file.

Where calls go comes from the environment, never argv: the server's URL (``--url-env``, or ``--url`` when it is no
secret) and each header the server authenticates with (``--header NAME=ENVVAR``) -- such as the agent token
``sim-mirror mcp`` minted, or a host application's own session credential. The URL must be a loopback one, and a proxy
named in the environment is ignored: a loopback call must not leave the machine. The client's own name, from
``initialize``, goes along in ``--client-header``, so a viewer can say which agent is at work.

The folders the client works in go along too, in ``--roots-header``, so a build runs in the folder the agent works in
-- a git worktree, say -- rather than the one this relay was started in. A client that offers MCP roots is asked for
them once it is initialized, again whenever it says they changed, and again before each tool call -- a client can move
into a worktree without saying so, as Claude Code's ``EnterWorktree`` does -- each sent as its path, percent-encoded,
the paths separated by commas. A call waits at most `ROOTS_WAIT_S` for that answer and then goes with the roots last
heard; a client that let it wait so long is not asked before a call again.

Anything that goes wrong -- no URL, the server not answering, a refusal -- becomes a tool result that says so, never a
crash of the server the client sees. A call that finds nothing listening -- one nothing received, so it is safe to send
again -- first asks where the server listens now (`Upstream.locate`), and goes there when that is another loopback
address: a server started again may have read another port. A relay given a way to bring the server back
(`Upstream.revive`, as ``sim-mirror mcp`` gives it) then asks it, asks where the server listens once more, and sends
the call once more when it answers that the server is back -- or answers with what it said instead. A relay with
neither says in one line how to start the server again.

A relay a host runs itself learns where SimMirror's daemon listens from its run folder (``--run-dir``): the daemon
writes ``daemon.json`` there once it listens. Only a regular file this user owns, in a folder nobody else may write
to, naming a process of this user's still running, is believed -- and then only its port: calls stay on 127.0.0.1,
with their own path. While ``sim-mirror mcp`` starts a daemon it holds the folder's start lock, and the relay waits
for it before it reads.

A tool call can take minutes -- a build, a long wait in `sim_act`. When the client asks to hear how one goes, with a
``progressToken`` in the call's ``_meta``, the relay sends ``notifications/progress`` about every `PROGRESS_S` while it
waits for the server's answer, so a client that restarts its timeout on progress keeps waiting. They are written by
their own thread, through the same lock as every other line, and never after the call's answer.

It says which SimMirror it relays for in ``serverInfo.version`` (``--server-version``), so a person can tell which
version a client is talking to.

Standard library only, importing nothing from SimMirror: a host runs it with its own interpreter, isolated
(``python -I relay.py``). `relay_command` is that command line.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import queue
import stat
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from typing import IO, Any
from urllib.parse import quote, unquote, urlsplit

PROTOCOL_VERSION = "2025-06-18"
DEFAULT_URL_ENV = "SIM_MIRROR_URL"
DEFAULT_SERVER_NAME = "sim-mirror"
#: What ``serverInfo.version`` says when the command line names no version.
DEFAULT_SERVER_VERSION = "1"
MANIFEST_PATH = "manifest"
CALL_PATH = "call"
MANIFEST_TIMEOUT_S = 10.0
#: A call can be a build that takes minutes; the server bounds each tool itself.
CALL_TIMEOUT_S = 900.0
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
CLIENT_NAME_MAX = 80
START_HINT = "Start these tools with `sim-mirror mcp`."
LIST_CHANGED = {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}
#: How many of the client's roots are sent along, and the longest path that is.
ROOTS_MAX = 8
ROOT_PATH_MAX = 1024
#: What this relay's own requests to the client are numbered after, so their answers are known for its own.
ROOTS_REQUEST = "sim-mirror-roots"
#: How long a tool call waits for the client's roots, asked for just before it, before it goes with those last heard.
ROOTS_WAIT_S = 2.0
#: What SimMirror's daemon writes in its run folder once it listens -- its pid, port and version -- and the lock held
#: while one is started there (`daemon.lifecycle`).
DAEMON_FILE = "daemon.json"
START_LOCK_FILE = "daemon-start.lock"
DAEMON_HOST = "127.0.0.1"
#: How often a tool call the client asked to hear about is said to be still running.
PROGRESS_S = 10.0

Opener = Callable[[urllib.request.Request, float], Any]
#: Brings the server back when nothing listens: None once it answers again, else what to tell the client.
Revive = Callable[[], "str | None"]
#: Where the server listens now -- the loopback URL calls go to -- or None when that is not known.
Locate = Callable[[], "str | None"]


def direct_opener() -> Opener:
    """An opener that ignores every proxy setting, for a URL that is always loopback."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return lambda request, timeout: opener.open(request, timeout=timeout)


def failure(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def refusal(exc: urllib.error.HTTPError) -> str:
    """What the server said when it refused, or its status when it said nothing readable."""
    try:
        body = json.loads(exc.read().decode("utf-8"))
    except (ValueError, UnicodeDecodeError, OSError):
        body = None
    said = (body.get("detail") or body.get("error")) if isinstance(body, dict) else None
    return said if isinstance(said, str) and said else f"HTTP {exc.code}"


def nothing_listening(exc: BaseException) -> bool:
    """Whether a request failed because nothing listens where it was sent: it never reached a server."""
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    return isinstance(reason, ConnectionRefusedError)


class Unreachable(Exception):
    """A call the server could not be reached for, saying so; `refused` when nothing listened."""

    def __init__(self, message: str, *, refused: bool) -> None:
        super().__init__(message)
        self.refused = refused


def is_loopback(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme in ("http", "https") and (parts.hostname or "") in LOOPBACK_HOSTS


def gone(url: str) -> str:
    """What a call nothing listened for is answered by a relay that cannot bring the server back: how to."""
    return (
        f"Nothing listens at {urlsplit(url).netloc} any more, so this call was not made. Start the server again -- "
        "`sim-mirror serve --detach` for SimMirror's daemon -- and call again, or reconnect this MCP server."
    )


def _private(found: os.stat_result) -> bool:
    """Whether this user owns it, and nobody else may write to it."""
    return found.st_uid == os.getuid() and not found.st_mode & (stat.S_IWGRP | stat.S_IWOTH)


def _wait_for_starts(folder: str) -> None:
    """Wait while a daemon is started from this run folder: whoever starts one holds its start lock meanwhile."""
    try:
        descriptor = os.open(os.path.join(folder, START_LOCK_FILE), os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return
    try:
        with contextlib.suppress(OSError):
            fcntl.flock(descriptor, fcntl.LOCK_SH)
    finally:
        os.close(descriptor)


def _number(value: object, below: int | None = None) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0 or (below is not None and value >= below):
        return None
    return value


def daemon_port(run_dir: str) -> int | None:
    """The port SimMirror's daemon listens on, from ``daemon.json`` in its run folder -- or None. Believed only from a
    regular file this user owns in a folder this user owns, neither writable by anyone else, naming a process of this
    user's still running."""
    folder = os.path.abspath(os.path.expanduser(run_dir))
    try:
        if not _private(os.stat(folder)):
            return None
        _wait_for_starts(folder)
        descriptor = os.open(os.path.join(folder, DAEMON_FILE), os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, encoding="utf-8") as handle:
            found = os.fstat(handle.fileno())
            if not stat.S_ISREG(found.st_mode) or not _private(found):
                return None
            document = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    pid, port = _number(document.get("pid")), _number(document.get("port"), 65536)
    if pid is None or port is None:
        return None
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    return port


def daemon_locate(run_dir: str, url: str) -> Locate:
    """Where calls to the daemon whose run folder is `run_dir` go now: `url`'s path, on the port it listens on."""
    path = urlsplit(url).path.rstrip("/")

    def locate() -> str | None:
        port = daemon_port(run_dir)
        return None if port is None else f"http://{DAEMON_HOST}:{port}{path}"

    return locate


def relay_command(
    *,
    python: str = sys.executable,
    url_env: str = DEFAULT_URL_ENV,
    headers: Mapping[str, str] | None = None,
    client_header: str | None = None,
    server_name: str = DEFAULT_SERVER_NAME,
    manifest_path: str = MANIFEST_PATH,
    call_path: str = CALL_PATH,
    roots_header: str | None = None,
    run_dir: str | os.PathLike[str] | None = None,
    server_version: str | None = None,
) -> list[str]:
    """The command line that runs this relay isolated, for a client's MCP configuration: secrets stay in env. With
    `run_dir`, SimMirror's run folder, a call that finds the daemon gone follows it to the port it listens on now. With
    `server_version` -- SimMirror's version, say -- ``serverInfo.version`` says it."""
    argv = [python, "-I", os.path.abspath(__file__), "--url-env", url_env, "--server-name", server_name]
    if server_version:
        argv += ["--server-version", server_version]
    argv += ["--manifest-path", manifest_path, "--call-path", call_path]
    for name, variable in (headers or {}).items():
        argv += ["--header", f"{name}={variable}"]
    if client_header:
        argv += ["--client-header", client_header]
    if roots_header:
        argv += ["--roots-header", roots_header]
    if run_dir is not None:
        argv += ["--run-dir", os.path.abspath(os.path.expanduser(run_dir))]
    return argv


def root_paths(result: object) -> list[str]:
    """The local folders a ``roots/list`` answer names, in its order: ``file:`` URIs only, at most `ROOTS_MAX`."""
    roots = result.get("roots") if isinstance(result, dict) else None
    found: list[str] = []
    for root in roots if isinstance(roots, list) else []:
        uri = root.get("uri") if isinstance(root, dict) else None
        parts = urlsplit(uri) if isinstance(uri, str) else None
        if parts is None or parts.scheme != "file" or parts.netloc not in ("", "localhost"):
            continue
        path = unquote(parts.path)
        if path.startswith("/") and len(path) <= ROOT_PATH_MAX and "\x00" not in path and path not in found:
            found.append(path)
    return found[:ROOTS_MAX]


def encode_roots(paths: Sequence[str]) -> str:
    """Folders as one header value: each percent-encoded, separated by commas."""
    return ",".join(quote(path, safe="/") for path in paths)


class Upstream:
    """The one place this relay sends anything."""

    def __init__(
        self,
        base: str,
        headers: Mapping[str, str],
        opener: Opener,
        *,
        manifest_path: str = MANIFEST_PATH,
        call_path: str = CALL_PATH,
        client_header: str | None = None,
        roots_header: str | None = None,
        problem: str | None = None,
        revive: Revive | None = None,
        locate: Locate | None = None,
    ) -> None:
        self.base = base.rstrip("/")
        self.headers = dict(headers)
        self.manifest_path = manifest_path.strip("/")
        self.call_path = call_path.strip("/")
        self.client_header = client_header
        self.roots_header = roots_header
        #: Why these tools cannot be used from here; None when they can.
        self.problem = problem
        #: How the server is brought back when nothing listens; None when this relay cannot.
        self.revive = revive
        #: Where the server listens now, asked when nothing listens where calls go; None when this relay cannot know.
        self.locate = locate
        #: Told as each tool call is handed on -- ``sim-mirror mcp`` holds its lease while calls come; None for nobody.
        self.on_call: Callable[[], None] | None = None
        #: What the client called itself, once it has said.
        self.client_name: str | None = None
        #: The folders the client works in, once it has said.
        self.roots: list[str] = []
        self._open = opener

    def relocate(self) -> bool:
        """Whether the server listens somewhere else now -- another loopback URL -- and calls go there from now on."""
        found = self.locate() if self.locate is not None else None
        if found is None or not is_loopback(found) or found.rstrip("/") == self.base:
            return False
        self.base = found.rstrip("/")
        return True

    def request(self, path: str, payload: dict[str, Any] | None, timeout: float) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {**self.headers, "Content-Type": "application/json"}
        if self.client_header and self.client_name:
            headers[self.client_header] = self.client_name
        if self.roots_header and self.roots:
            headers[self.roots_header] = encode_roots(self.roots)
        request = urllib.request.Request(
            f"{self.base}/{path}", data=data, method="GET" if data is None else "POST", headers=headers
        )
        with self._open(request, timeout) as response:
            answer = json.loads(response.read().decode("utf-8"))
        if not isinstance(answer, dict):
            raise ValueError("the SimMirror server answered with something that is not an object")
        return answer


def tool_signature(manifest: Mapping[str, Any]) -> list[str]:
    """Definitions, not just names: a changed schema or description also invalidates a client's cached list."""
    tools = manifest.get("tools")
    return (
        [json.dumps(tool, sort_keys=True) for tool in tools if isinstance(tool, dict)]
        if isinstance(tools, list)
        else []
    )


Notify = Callable[[dict[str, Any]], None]


def progress_token(params: Mapping[str, Any]) -> str | int | None:
    """The token a client asked to hear a request's progress by, in its ``_meta``; None when it did not ask."""
    meta = params.get("_meta")
    token = meta.get("progressToken") if isinstance(meta, dict) else None
    return token if isinstance(token, (str, int)) and not isinstance(token, bool) else None


class Progress:
    """While a tool call runs, ``notifications/progress`` for its token every `every_s`, from a thread of its own: how
    many times it was said, and how long the call has run. Stopped -- nothing more said -- before the call is
    answered."""

    def __init__(self, token: str | int, tool: object, notify: Notify, every_s: float = PROGRESS_S) -> None:
        self._token = token
        self._tool = tool if isinstance(tool, str) else "the call"
        self._notify = notify
        self._every = every_s
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._run, name="relay-progress", daemon=True)

    def __enter__(self) -> Progress:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._done.set()
        self._thread.join()

    def _run(self) -> None:
        said = 0
        while not self._done.wait(self._every):
            said += 1
            self._notify(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/progress",
                    "params": {
                        "progressToken": self._token,
                        "progress": said,
                        "message": f"{self._tool} is still running ({said * self._every:g} s)",
                    },
                }
            )


class Relay:
    """The MCP methods a tools-only server answers, each by asking the server."""

    def __init__(
        self,
        upstream: Upstream,
        *,
        server_name: str = DEFAULT_SERVER_NAME,
        server_version: str = DEFAULT_SERVER_VERSION,
        progress_s: float = PROGRESS_S,
    ) -> None:
        self.upstream = upstream
        self.server_name = server_name
        self.server_version = server_version
        #: How a notification reaches the client while a call runs -- `serve` points it at its locked writer; None, and
        #: none is sent -- and how often a call the client asked to hear about says it is still running.
        self.notify: Notify | None = None
        self._progress_s = progress_s
        #: The definitions the client was last given; None until it has asked.
        self._listed: list[str] | None = None
        #: Whether the client offers its roots, and the id of the last time it was asked for them.
        self._roots_offered = False
        self._asked = 0
        #: The tool calls waiting for the client's answer to that request, and whether a call is to wait for one.
        self._held: list[Mapping[str, Any]] = []
        self._ask_before_calls = True

    def _fetch(self, *, follow: bool = True) -> tuple[dict[str, Any] | None, str]:
        """The server's manifest now -- asked where it listens now when nothing listened -- or None, and why not."""
        if self.upstream.problem:
            return None, self.upstream.problem
        try:
            return self.upstream.request(self.upstream.manifest_path, None, MANIFEST_TIMEOUT_S), ""
        except urllib.error.HTTPError as exc:
            return None, f"The SimMirror server refused this client: {refusal(exc)}"
        except (urllib.error.URLError, OSError, ValueError) as exc:
            if follow and nothing_listening(exc) and self.upstream.relocate():
                return self._fetch(follow=False)
            return None, f"The SimMirror server could not be reached: {exc}"

    def manifest(self) -> dict[str, Any]:
        """The tools and instructions as the server offers them now -- or none, and why."""
        found, why = self._fetch()
        return found if found is not None else {"tools": [], "instructions": why}

    def call(self, params: Mapping[str, Any]) -> dict[str, Any]:
        if self.upstream.problem:
            return failure(self.upstream.problem)
        if self.upstream.on_call is not None:
            self.upstream.on_call()
        payload = {"name": params.get("name"), "arguments": params.get("arguments") or {}}
        answer = self._attempt(payload)
        if answer is None and self.upstream.relocate():
            answer = self._attempt(payload)
        if answer is not None:
            return answer
        if self.upstream.revive is None:
            return failure(gone(self.upstream.base))
        said = self.upstream.revive()
        if said is not None:
            return failure(said)
        self.upstream.relocate()
        try:
            return self._send(payload)
        except Unreachable as exc:
            return failure(str(exc))

    def _attempt(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """The server's answer to a call, or why it could not be reached -- or None when nothing listened for it."""
        try:
            return self._send(payload)
        except Unreachable as exc:
            return None if exc.refused else failure(str(exc))

    def _send(self, payload: dict[str, Any]) -> dict[str, Any]:
        """The server's answer to a call, a refusal as a result; raises `Unreachable` when it could not be reached."""
        try:
            return self.upstream.request(self.upstream.call_path, payload, CALL_TIMEOUT_S)
        except urllib.error.HTTPError as exc:
            return failure(f"The SimMirror server refused the call: {refusal(exc)}")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            message = f"The SimMirror server could not be reached: {exc}"
            raise Unreachable(message, refused=nothing_listening(exc)) from exc

    def _changes(self) -> list[dict[str, Any]]:
        """A notification when the tools no longer match what the client was last given -- once for each change. A
        manifest that cannot be had changes nothing."""
        if self._listed is None:
            return []
        found, _ = self._fetch()
        if found is None or tool_signature(found) == self._listed:
            return []
        self._listed = tool_signature(found)
        return [dict(LIST_CHANGED)]

    def _introduce(self, params: Mapping[str, Any]) -> None:
        info = params.get("clientInfo")
        name = info.get("name") if isinstance(info, dict) else None
        if isinstance(name, str) and " ".join(name.split()):
            self.upstream.client_name = " ".join(name.split())[:CLIENT_NAME_MAX]
        capabilities = params.get("capabilities")
        self._roots_offered = isinstance(capabilities, dict) and isinstance(capabilities.get("roots"), dict)

    def _ask_for_roots(self) -> list[dict[str, Any]]:
        """A request for the client's roots, when it offers them and the server is to hear of them."""
        if not self._roots_offered or not self.upstream.roots_header:
            return []
        self._asked += 1
        return [{"jsonrpc": "2.0", "id": f"{ROOTS_REQUEST}-{self._asked}", "method": "roots/list"}]

    def _answered(self, message: Mapping[str, Any]) -> list[dict[str, Any]]:
        """The client's answer to this relay's latest request for its roots, then the replies to the calls that waited
        for it; any other answer is let go."""
        if message.get("id") != f"{ROOTS_REQUEST}-{self._asked}":
            return []
        if "result" in message:
            self.upstream.roots = root_paths(message["result"])
        return self._release()

    @property
    def waiting(self) -> bool:
        """Whether tool calls are waiting for the client's roots."""
        return bool(self._held)

    def expire(self) -> list[dict[str, Any]]:
        """The replies to the calls that waited too long for the client's roots, made with those last heard. A client
        this slow is not asked before a call again."""
        self._ask_before_calls = False
        return self._release()

    def _release(self) -> list[dict[str, Any]]:
        held, self._held = self._held, []
        return [reply for message in held for reply in self._called(message)]

    def _hold(self, message: Mapping[str, Any]) -> list[dict[str, Any]] | None:
        """A request for the client's roots, which this call waits for -- nothing more when calls already wait for
        one -- or None when the call goes now."""
        if self._held:
            self._held.append(message)
            return []
        asking = self._ask_for_roots() if self._ask_before_calls and not self.upstream.problem else []
        if not asking:
            return None
        self._held.append(message)
        return asking

    def _called(self, message: Mapping[str, Any]) -> list[dict[str, Any]]:
        """The reply to a tool call, then a notification when the tools changed with it. A call the client asked to hear
        about says it is still running while it waits."""
        raw = message.get("params")
        params: Mapping[str, Any] = raw if isinstance(raw, dict) else {}
        token = progress_token(params)
        if token is None or self.notify is None:
            result = self.call(params)
        else:
            with Progress(token, params.get("name"), self.notify, self._progress_s):
                result = self.call(params)
        return [{"jsonrpc": "2.0", "id": message["id"], "result": result}, *self._changes()]

    def handle(self, message: Mapping[str, Any]) -> list[dict[str, Any]]:
        """The reply to one message, then any notification or request it leads to -- for a tool call that waits for
        the client's roots, only the request for them, and for the client's answer, the replies to the calls that
        waited."""
        method = message.get("method")
        if "id" not in message:
            changed = method in ("notifications/initialized", "notifications/roots/list_changed")
            return self._ask_for_roots() if changed else []
        if method is None and ("result" in message or "error" in message):
            return self._answered(message)
        if method == "tools/call":
            held = self._hold(message)
            return self._called(message) if held is None else held
        raw = message.get("params")
        params: Mapping[str, Any] = raw if isinstance(raw, dict) else {}
        if method == "initialize":
            self._introduce(params)
            result: dict[str, Any] = {
                "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": {"name": self.server_name, "version": self.server_version},
                "instructions": self.manifest().get("instructions", ""),
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            found, problem = self._fetch()
            # A list that could not be had gave the client none: the next call that finds tools tells it they changed.
            self._listed = tool_signature(found) if found is not None else []
            if found is None:
                return [{"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32603, "message": problem}}]
            result = {"tools": (found or {}).get("tools", [])}
        else:
            return [
                {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "error": {"code": -32601, "message": f"method not found: {method}"},
                }
            ]
        return [{"jsonrpc": "2.0", "id": message["id"], "result": result}]


def _read(stdin: IO[str], lines: queue.Queue[str | None]) -> None:
    """Every line of `stdin`, then None once it closes -- or can no longer be read."""
    try:
        for line in stdin:
            lines.put(line)
    finally:
        lines.put(None)


class Writer:
    """Lines to the client, each message whole, whichever thread writes them."""

    def __init__(self, stdout: IO[str]) -> None:
        self._stdout = stdout
        self._lock = threading.Lock()

    def __call__(self, replies: Sequence[dict[str, Any]]) -> None:
        with self._lock:
            for reply in replies:
                self._stdout.write(json.dumps(reply) + "\n")
            self._stdout.flush()

    def one(self, message: dict[str, Any]) -> None:
        self([message])


def serve(stdin: IO[str], stdout: IO[str], relay: Relay, *, wait_s: float = ROOTS_WAIT_S) -> None:
    """Answer newline-delimited JSON-RPC until stdin closes. Calls waiting for the client's roots go once `wait_s` has
    passed without them; any still waiting when stdin closes are not made, there being nobody left to answer."""
    lines: queue.Queue[str | None] = queue.Queue()
    threading.Thread(target=_read, args=(stdin, lines), name="relay-stdin", daemon=True).start()
    write = Writer(stdout)
    relay.notify = write.one
    deadline: float | None = None
    while True:
        if not relay.waiting:
            deadline = None
        elif deadline is None:
            deadline = time.monotonic() + wait_s
        try:
            line = lines.get() if deadline is None else lines.get(timeout=max(0.0, deadline - time.monotonic()))
        except queue.Empty:
            write(relay.expire())
            continue
        if line is None:
            return
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except ValueError:
            replies = [{"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "not JSON"}}]
        else:
            replies = (
                relay.handle(message)
                if isinstance(message, dict)
                else [{"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "not a request"}}]
            )
        write(replies)


def _header_spec(value: str) -> tuple[str, str]:
    name, separator, variable = value.partition("=")
    if not separator or not name.strip() or not variable.strip():
        raise argparse.ArgumentTypeError(f"a header is NAME=ENVVAR, not {value!r}")
    return name.strip(), variable.strip()


def parser() -> argparse.ArgumentParser:
    made = argparse.ArgumentParser(description="Relay an MCP client's tool calls to a SimMirror server.")
    made.add_argument("--url-env", default=DEFAULT_URL_ENV, help="the variable holding the server's tools URL")
    made.add_argument("--url", help="the server's tools URL itself, when it holds no secret")
    made.add_argument("--header", action="append", type=_header_spec, default=[], help="NAME=ENVVAR, repeatable")
    made.add_argument("--client-header", help="the header the client's own name is sent in")
    made.add_argument("--roots-header", help="the header the folders the client works in are sent in")
    made.add_argument("--server-name", default=DEFAULT_SERVER_NAME)
    made.add_argument("--server-version", default=DEFAULT_SERVER_VERSION, help="what serverInfo.version says")
    made.add_argument("--manifest-path", default=MANIFEST_PATH)
    made.add_argument("--call-path", default=CALL_PATH)
    made.add_argument("--run-dir", help="SimMirror's run folder, to follow its daemon to the port it listens on now")
    return made


def upstream_for(
    args: argparse.Namespace,
    env: Mapping[str, str],
    opener: Opener,
    *,
    revive: Revive | None = None,
    locate: Locate | None = None,
) -> Upstream:
    """Where this relay sends calls, as its flags and environment say -- and why it cannot, when it cannot. Without
    `locate`, a relay given ``--run-dir`` reads where the daemon listens now from that folder."""
    url = (args.url or env.get(args.url_env, "")).strip()
    if locate is None and args.run_dir:
        locate = daemon_locate(args.run_dir, url)
    problem = None
    headers: dict[str, str] = {}
    for name, variable in args.header:
        headers[name] = env.get(variable, "")
        if not headers[name] and problem is None:
            problem = f"{variable} is not set, so these tools cannot be used here. {START_HINT}"
    if not url:
        problem = (
            f"No SimMirror server was given ({args.url_env} is not set), so these tools cannot be used here. "
            f"{START_HINT}"
        )
    elif not is_loopback(url):
        problem = f"{url} is not a loopback URL, and these tools only talk to this machine."
    return Upstream(
        url,
        headers,
        opener,
        manifest_path=args.manifest_path,
        call_path=args.call_path,
        client_header=args.client_header,
        roots_header=args.roots_header,
        problem=problem,
        revive=revive,
        locate=locate,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    stdin: IO[str] | None = None,
    stdout: IO[str] | None = None,
    opener: Opener | None = None,
    revive: Revive | None = None,
    locate: Locate | None = None,
    on_call: Callable[[], None] | None = None,
) -> int:
    args = parser().parse_args(argv)
    upstream = upstream_for(
        args, os.environ if env is None else env, opener or direct_opener(), revive=revive, locate=locate
    )
    upstream.on_call = on_call
    serve(
        sys.stdin if stdin is None else stdin,
        sys.stdout if stdout is None else stdout,
        Relay(upstream, server_name=args.server_name, server_version=args.server_version),
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - the entry point an MCP client runs
    raise SystemExit(main())
