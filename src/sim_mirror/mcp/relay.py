#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""An MCP server over stdio that knows no tools: it asks a SimMirror server which there are, and hands it every call.

An MCP client -- Claude Code, Codex, Cursor -- starts this over stdio. It lists the tools the server offers and hands
every call to the server, which does the work, so the tools, their checks and what a viewer shows live in one place,
and all a client can reach is this file.

Where calls go comes from the environment, never argv: the server's URL (``--url-env``, or ``--url`` when it is no
secret) and each header the server authenticates with (``--header NAME=ENVVAR``) -- such as the agent token
``sim-mirror mcp`` minted, or a host application's own session credential. The URL must be a loopback one, and a proxy
named in the environment is ignored: a loopback call must not leave the machine. The client's own name, from
``initialize``, goes along in ``--client-header``, so a viewer can say which agent is at work.

Anything that goes wrong -- no URL, the server not answering, a refusal -- becomes a tool result that says so, never a
crash of the server the client sees.

Standard library only, importing nothing from SimMirror: a host runs it with its own interpreter, isolated
(``python -I relay.py``). `relay_command` is that command line.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from typing import IO, Any
from urllib.parse import urlsplit

PROTOCOL_VERSION = "2025-06-18"
DEFAULT_URL_ENV = "SIM_MIRROR_URL"
DEFAULT_SERVER_NAME = "sim-mirror"
MANIFEST_PATH = "manifest"
CALL_PATH = "call"
MANIFEST_TIMEOUT_S = 10.0
#: A call can be a build that takes minutes; the server bounds each tool itself.
CALL_TIMEOUT_S = 900.0
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
CLIENT_NAME_MAX = 80
START_HINT = "Start these tools with `sim-mirror mcp`."

Opener = Callable[[urllib.request.Request, float], Any]


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


def is_loopback(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme in ("http", "https") and (parts.hostname or "") in LOOPBACK_HOSTS


def relay_command(
    *,
    python: str = sys.executable,
    url_env: str = DEFAULT_URL_ENV,
    headers: Mapping[str, str] | None = None,
    client_header: str | None = None,
    server_name: str = DEFAULT_SERVER_NAME,
    manifest_path: str = MANIFEST_PATH,
    call_path: str = CALL_PATH,
) -> list[str]:
    """The command line that runs this relay isolated, for a client's MCP configuration: secrets stay in env."""
    argv = [python, "-I", os.path.abspath(__file__), "--url-env", url_env, "--server-name", server_name]
    argv += ["--manifest-path", manifest_path, "--call-path", call_path]
    for name, variable in (headers or {}).items():
        argv += ["--header", f"{name}={variable}"]
    if client_header:
        argv += ["--client-header", client_header]
    return argv


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
        problem: str | None = None,
    ) -> None:
        self.base = base.rstrip("/")
        self.headers = dict(headers)
        self.manifest_path = manifest_path.strip("/")
        self.call_path = call_path.strip("/")
        self.client_header = client_header
        #: Why these tools cannot be used from here; None when they can.
        self.problem = problem
        #: What the client called itself, once it has said.
        self.client_name: str | None = None
        self._open = opener

    def request(self, path: str, payload: dict[str, Any] | None, timeout: float) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {**self.headers, "Content-Type": "application/json"}
        if self.client_header and self.client_name:
            headers[self.client_header] = self.client_name
        request = urllib.request.Request(
            f"{self.base}/{path}", data=data, method="GET" if data is None else "POST", headers=headers
        )
        with self._open(request, timeout) as response:
            answer = json.loads(response.read().decode("utf-8"))
        if not isinstance(answer, dict):
            raise ValueError("the SimMirror server answered with something that is not an object")
        return answer


class Relay:
    """The MCP methods a tools-only server answers, each by asking the server."""

    def __init__(self, upstream: Upstream, *, server_name: str = DEFAULT_SERVER_NAME) -> None:
        self.upstream = upstream
        self.server_name = server_name
        self._manifest: dict[str, Any] | None = None

    def manifest(self) -> dict[str, Any]:
        """The tools and instructions, asked for once -- and asked again after a failure."""
        if self._manifest is not None:
            return self._manifest
        if self.upstream.problem:
            return {"tools": [], "instructions": self.upstream.problem}
        try:
            self._manifest = self.upstream.request(self.upstream.manifest_path, None, MANIFEST_TIMEOUT_S)
        except urllib.error.HTTPError as exc:
            return {"tools": [], "instructions": f"The SimMirror server refused this client: {refusal(exc)}"}
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return {"tools": [], "instructions": f"The SimMirror server could not be reached: {exc}"}
        return self._manifest

    def call(self, params: Mapping[str, Any]) -> dict[str, Any]:
        if self.upstream.problem:
            return failure(self.upstream.problem)
        payload = {"name": params.get("name"), "arguments": params.get("arguments") or {}}
        try:
            return self.upstream.request(self.upstream.call_path, payload, CALL_TIMEOUT_S)
        except urllib.error.HTTPError as exc:
            return failure(f"The SimMirror server refused the call: {refusal(exc)}")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return failure(f"The SimMirror server could not be reached: {exc}")

    def _introduce(self, params: Mapping[str, Any]) -> None:
        info = params.get("clientInfo")
        name = info.get("name") if isinstance(info, dict) else None
        if isinstance(name, str) and " ".join(name.split()):
            self.upstream.client_name = " ".join(name.split())[:CLIENT_NAME_MAX]

    def handle(self, message: Mapping[str, Any]) -> dict[str, Any] | None:
        """The reply to one message; None for a notification, which gets none."""
        if "id" not in message:
            return None
        method = message.get("method")
        raw = message.get("params")
        params: Mapping[str, Any] = raw if isinstance(raw, dict) else {}
        if method == "initialize":
            self._introduce(params)
            result: dict[str, Any] = {
                "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": self.server_name, "version": "1"},
                "instructions": self.manifest().get("instructions", ""),
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": self.manifest().get("tools", [])}
        elif method == "tools/call":
            result = self.call(params)
        else:
            return {
                "jsonrpc": "2.0",
                "id": message["id"],
                "error": {"code": -32601, "message": f"method not found: {method}"},
            }
        return {"jsonrpc": "2.0", "id": message["id"], "result": result}


def serve(stdin: IO[str], stdout: IO[str], relay: Relay) -> None:
    """Answer newline-delimited JSON-RPC until stdin closes."""
    for line in stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except ValueError:
            reply: dict[str, Any] | None = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "not JSON"},
            }
        else:
            reply = (
                relay.handle(message)
                if isinstance(message, dict)
                else {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "not a request"}}
            )
        if reply is not None:
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()


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
    made.add_argument("--server-name", default=DEFAULT_SERVER_NAME)
    made.add_argument("--manifest-path", default=MANIFEST_PATH)
    made.add_argument("--call-path", default=CALL_PATH)
    return made


def upstream_for(args: argparse.Namespace, env: Mapping[str, str], opener: Opener) -> Upstream:
    """Where this relay sends calls, as its flags and environment say -- and why it cannot, when it cannot."""
    url = (args.url or env.get(args.url_env, "")).strip()
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
        problem=problem,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    stdin: IO[str] | None = None,
    stdout: IO[str] | None = None,
    opener: Opener | None = None,
) -> int:
    args = parser().parse_args(argv)
    upstream = upstream_for(args, os.environ if env is None else env, opener or direct_opener())
    serve(
        sys.stdin if stdin is None else stdin,
        sys.stdout if stdout is None else stdout,
        Relay(upstream, server_name=args.server_name),
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - the entry point an MCP client runs
    raise SystemExit(main())
