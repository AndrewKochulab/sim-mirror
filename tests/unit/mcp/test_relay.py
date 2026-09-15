# SPDX-License-Identifier: Apache-2.0
"""The relay an MCP client runs: every tool call handed to a loopback SimMirror server, credentials from the
environment, and every failure a result the client can read."""

from __future__ import annotations

import ast
import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from sim_mirror.mcp import relay
from sim_mirror.mcp.relay import direct_opener, main, relay_command

ENV = {
    "SIM_MIRROR_URL": "http://127.0.0.1:7466/api/v1/agent/",
    "SIM_MIRROR_TOKEN": "secret-token",
    "SIM_MIRROR_SCOPE": "tp-1",
}
FLAGS = [
    "--header",
    "X-SimMirror-Token=SIM_MIRROR_TOKEN",
    "--header",
    "X-SimMirror-Scope=SIM_MIRROR_SCOPE",
    "--client-header",
    "X-SimMirror-Client",
]
MANIFEST = {"tools": [{"name": "sim_snapshot", "inputSchema": {"type": "object"}}], "instructions": "look first"}
DONE = {"content": [{"type": "text", "text": 'ok tap e2 "General" (201,319)'}], "isError": False}


class Response:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def read(self) -> bytes:
        return self.body

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class Opener:
    """Answers each request in turn: an object as JSON, bytes as they are, an exception by raising it."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.requests: list[tuple[urllib.request.Request, float]] = []

    def __call__(self, request: urllib.request.Request, timeout: float) -> Response:
        self.requests.append((request, timeout))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return Response(answer if isinstance(answer, bytes) else json.dumps(answer).encode())


def refused(body: bytes, code: int = 403) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://127.0.0.1:7466/api/v1/agent/call", code, "Forbidden", {}, io.BytesIO(body))  # type: ignore[arg-type]


def run(messages: list[Any], opener: Opener, *, env: dict[str, str] = ENV, flags: list[str] = FLAGS) -> list[Any]:
    lines = "".join(message if isinstance(message, str) else json.dumps(message) + "\n" for message in messages)
    stdout = io.StringIO()
    assert main(flags, env=env, stdin=io.StringIO(lines), stdout=stdout, opener=opener) == 0
    return [json.loads(line) for line in stdout.getvalue().splitlines()]


def test_a_client_lists_the_servers_tools_as_they_are_now_and_every_call_goes_to_the_server_as_that_client() -> None:
    opener = Opener(MANIFEST, MANIFEST, DONE, MANIFEST)
    initialize = {"protocolVersion": "2025-03-26", "clientInfo": {"name": "  claude-code \n"}}
    replies = run(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": initialize},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            "\n",
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "ping"},
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "sim_act", "arguments": {"steps": []}},
            },
        ],
        opener,
    )
    assert replies[0] == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "protocolVersion": "2025-03-26",
            "capabilities": {"tools": {"listChanged": True}},
            "serverInfo": {"name": "sim-mirror", "version": "1"},
            "instructions": "look first",
        },
    }
    assert replies[1:] == [
        {"jsonrpc": "2.0", "id": 2, "result": {"tools": MANIFEST["tools"]}},
        {"jsonrpc": "2.0", "id": 3, "result": {}},
        {"jsonrpc": "2.0", "id": 4, "result": DONE},
    ]
    (_, _), (listed, list_timeout), (called, call_timeout), (checked, _) = opener.requests
    assert checked.full_url == listed.full_url
    assert listed.full_url == "http://127.0.0.1:7466/api/v1/agent/manifest" and listed.get_method() == "GET"
    assert listed.get_header("X-simmirror-token") == "secret-token" and listed.get_header("X-simmirror-scope") == "tp-1"
    assert listed.get_header("X-simmirror-client") == "claude-code" and list_timeout == relay.MANIFEST_TIMEOUT_S
    assert called.full_url == "http://127.0.0.1:7466/api/v1/agent/call" and called.get_method() == "POST"
    assert json.loads(called.data) == {"name": "sim_act", "arguments": {"steps": []}}  # type: ignore[arg-type]
    assert call_timeout == relay.CALL_TIMEOUT_S


def test_a_host_mounts_it_under_its_own_paths_and_names_it_its_own_way() -> None:
    opener = Opener(MANIFEST)
    flags = ["--url-env", "HOST_URL", "--server-name", "host-simulator", "--manifest-path", "/simulator/manifest"]
    replies = run(
        [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"clientInfo": {"name": 7}}}],
        opener,
        env={"HOST_URL": "http://localhost:8080/api/hooks/"},
        flags=flags,
    )
    assert replies[0]["result"]["serverInfo"]["name"] == "host-simulator"
    ((request, _),) = opener.requests
    assert request.full_url == "http://localhost:8080/api/hooks/simulator/manifest"
    assert request.get_header("X-simmirror-client") is None


def test_what_is_not_a_request_it_knows_is_answered_as_json_rpc_says() -> None:
    replies = run(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": "not an object"},
            {"jsonrpc": "2.0", "id": 2, "method": "resources/list"},
            "{not json\n",
            "[1, 2]\n",
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "sim_snapshot"}},
        ],
        Opener(MANIFEST, DONE),
    )
    assert replies[0]["result"]["protocolVersion"] == relay.PROTOCOL_VERSION
    assert replies[1] == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32601, "message": "method not found: resources/list"},
    }
    assert replies[2] == {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "not JSON"}}
    assert replies[3] == {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "not a request"}}
    assert replies[4]["result"] == DONE


@pytest.mark.parametrize(
    ("env", "problem"),
    [
        ({}, "No SimMirror server was given (SIM_MIRROR_URL is not set)"),
        ({**ENV, "SIM_MIRROR_TOKEN": ""}, "SIM_MIRROR_TOKEN is not set, so these tools cannot be used here."),
        (
            {**ENV, "SIM_MIRROR_URL": "https://sim.example/api/v1/agent"},
            "https://sim.example/api/v1/agent is not a loopback URL",
        ),
    ],
)
def test_a_client_without_a_usable_server_has_no_tools_and_is_told_why(env: dict[str, str], problem: str) -> None:
    opener = Opener()
    replies = run(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "sim_snapshot"}},
        ],
        opener,
        env=env,
    )
    assert replies[0]["result"]["instructions"].startswith(problem)
    assert replies[1]["result"] == {"tools": []}
    assert replies[2]["result"]["isError"] is True and replies[2]["result"]["content"][0]["text"].startswith(problem)
    assert opener.requests == []


def test_a_refusal_or_an_unreachable_server_is_a_result_and_a_failed_list_is_asked_again() -> None:
    opener = Opener(
        refused(b'{"detail": "this token is not for that scope"}'),
        urllib.error.URLError("connection refused"),
        MANIFEST,
        refused(b"<html>no</html>"),
        MANIFEST,
        refused(b'{"ok": false, "error": "cross-origin request refused"}'),
        MANIFEST,
        urllib.error.URLError("timed out"),
        MANIFEST,
        b"[1]",
        MANIFEST,
    )
    replies = run(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 2, "method": "initialize"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
            *({"jsonrpc": "2.0", "id": n, "method": "tools/call", "params": {"name": "sim_act"}} for n in range(4, 8)),
        ],
        opener,
    )
    assert replies[0]["result"] == {"tools": []}
    assert "could not be reached: <urlopen error connection refused>" in replies[1]["result"]["instructions"]
    assert replies[2]["result"] == {"tools": MANIFEST["tools"]}
    assert [reply["result"]["content"][0]["text"] for reply in replies[3:]] == [
        "The SimMirror server refused the call: HTTP 403",
        "The SimMirror server refused the call: cross-origin request refused",
        "The SimMirror server could not be reached: <urlopen error timed out>",
        "The SimMirror server could not be reached: the SimMirror server answered with something that is not an object",
    ]
    assert all(reply["result"]["isError"] for reply in replies[3:])


def test_a_refused_manifest_says_what_the_server_said() -> None:
    replies = run(
        [{"jsonrpc": "2.0", "id": 1, "method": "initialize"}],
        Opener(refused(b'{"detail": "this token is not an agent\'s"}')),
    )
    assert (
        replies[0]["result"]["instructions"] == "The SimMirror server refused this client: this token is not an agent's"
    )


def test_the_relay_opens_the_loopback_url_with_no_proxy_whatever_the_environment_says(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    class Built:
        def open(self, request: object, timeout: float) -> str:
            seen["opened"] = (request, timeout)
            return "response"

    def build_opener(*handlers: object) -> Built:
        seen["handlers"] = handlers
        return Built()

    monkeypatch.setenv("http_proxy", "http://proxy.example:3128")
    monkeypatch.setattr(relay.urllib.request, "build_opener", build_opener)
    opener = direct_opener()
    (handler,) = seen["handlers"]
    assert isinstance(handler, urllib.request.ProxyHandler) and handler.proxies == {}
    assert opener("request", 5.0) == "response" and seen["opened"] == ("request", 5.0)  # type: ignore[arg-type]


def test_main_reads_the_real_streams_and_environment_and_refuses_a_malformed_header(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stdout = io.StringIO()
    monkeypatch.setattr(relay.sys, "stdin", io.StringIO('{"jsonrpc": "2.0", "id": 1, "method": "ping"}\n'))
    monkeypatch.setattr(relay.sys, "stdout", stdout)
    assert main(["--url", "http://127.0.0.1:7466/api/v1/agent"]) == 0
    assert json.loads(stdout.getvalue()) == {"jsonrpc": "2.0", "id": 1, "result": {}}
    for spec in ("no-equals", "=TOKEN", "Name="):
        with pytest.raises(SystemExit):
            main(["--header", spec], env=ENV, stdin=io.StringIO(""), stdout=io.StringIO())
    assert "a header is NAME=ENVVAR" in capsys.readouterr().err


def test_a_host_gets_the_command_line_that_runs_the_relay_isolated_with_its_secrets_in_the_environment() -> None:
    argv = relay_command(
        python="/usr/bin/python3",
        url_env="HOST_URL",
        headers={"X-Session-Key": "HOST_KEY", "X-Hook-Token": "HOST_TOKEN"},
        client_header="X-Client",
        server_name="host-simulator",
        manifest_path="/simulator/manifest",
        call_path="/simulator/call",
    )
    assert argv[:3] == ["/usr/bin/python3", "-I", str(Path(relay.__file__).resolve())]
    assert argv[3:] == [
        "--url-env", "HOST_URL", "--server-name", "host-simulator", "--manifest-path", "/simulator/manifest",
        "--call-path", "/simulator/call", "--header", "X-Session-Key=HOST_KEY", "--header", "X-Hook-Token=HOST_TOKEN",
        "--client-header", "X-Client",
    ]  # fmt: skip
    assert relay_command()[3:] == [
        "--url-env", "SIM_MIRROR_URL", "--server-name", "sim-mirror",
        "--manifest-path", "manifest", "--call-path", "call",
    ]  # fmt: skip
    assert relay.is_loopback("http://[::1]:7466/x") and not relay.is_loopback("file:///tmp/x")


def test_the_relay_uses_only_the_standard_library() -> None:
    tree = ast.parse(Path(relay.__file__).read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    }
    imported |= {
        node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    }
    assert imported - {"__future__"} <= set(sys.stdlib_module_names)


def test_the_client_hears_when_the_tools_it_was_given_change_and_only_then() -> None:
    reduced = {"tools": [], "instructions": "The iOS Simulator is off for this project."}
    opener = Opener(
        MANIFEST,
        DONE,
        MANIFEST,
        DONE,
        reduced,
        DONE,
        reduced,
        DONE,
        urllib.error.URLError("gone"),
        DONE,
        {"tools": [7], "instructions": "odd"},
    )
    calls = (
        {"jsonrpc": "2.0", "id": n, "method": "tools/call", "params": {"name": "sim_snapshot"}} for n in range(2, 7)
    )
    replies = run([{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, *calls], opener)
    assert [reply.get("id", reply.get("method")) for reply in replies] == [
        1, 2, 3, "notifications/tools/list_changed", 4, 5, 6,
    ]  # fmt: skip
