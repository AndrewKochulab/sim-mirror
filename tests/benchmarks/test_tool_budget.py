# SPDX-License-Identifier: Apache-2.0
"""The token-budget benchmark, driven through SimMirror's real relay over a fake server and fake pipes."""

from __future__ import annotations

import base64
import io
import json
import subprocess
from collections.abc import Callable
from typing import Any

import pytest

import tool_budget
from sim_mirror.mcp.relay import Relay, Upstream
from sim_mirror.testing.fakes import tiny_jpeg
from tool_budget import (
    PROBES,
    BenchmarkError,
    McpClient,
    Probe,
    Row,
    Samples,
    main,
    measure,
    percentile,
    summarize,
    table,
)

TOOLS = ["sim_device", "sim_snapshot", "sim_screenshot", "sim_act", "sim_app"]


def answers(tools: list[str]) -> Callable[[Any, float], io.BytesIO]:
    """The agent routes of a server offering `tools`: text for most, a JPEG for a screenshot."""

    def opener(request: Any, timeout: float) -> io.BytesIO:
        if request.data is None:
            body: Any = {"tools": [{"name": name} for name in tools], "instructions": "Look, then act."}
        else:
            call = json.loads(request.data)
            if call["name"] == "sim_screenshot":
                image = base64.b64encode(tiny_jpeg(call["arguments"]["width"], 868)).decode("ascii")
                body = {"content": [{"type": "image", "data": image, "mimeType": "image/jpeg"}]}
            elif call["arguments"].get("mode") == "diff":
                body = {"content": [{"type": "text", "text": "Nothing changed."}], "isError": False}
            else:
                body = {"content": [{"type": "text", "text": "x" * 400}]}
        return io.BytesIO(json.dumps(body).encode("utf-8"))

    return opener


class Pipe(io.StringIO):
    """The server's stdin: each line written is answered at once on its stdout, as the relay would."""

    def __init__(self, relay: Relay, out: io.StringIO, noise: bool = False) -> None:
        super().__init__()
        self.relay = relay
        self.out = out
        self.noise = noise

    def write(self, text: str) -> int:
        for line in text.splitlines():
            for reply in self.relay.handle(json.loads(line)):
                if self.noise:
                    self.out.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/message"}) + "\n")
                self.out.write(json.dumps(reply) + "\n")
        return len(text)


class FakeProcess:
    def __init__(
        self, tools: list[str] = TOOLS, *, hangs: bool = False, pipes: bool = True, noise: bool = False
    ) -> None:
        self.reader = _Reader()
        relay = Relay(Upstream("http://127.0.0.1:7466/api/v1/agent", {}, answers(tools)))
        self.writer = Pipe(relay, self.reader.buffer, noise)
        self.hangs = hangs
        self.pipes = pipes
        self.killed = False
        self.waits: list[float | None] = []

    @property
    def stdin(self) -> Any:
        return self.writer if self.pipes else None

    @property
    def stdout(self) -> Any:
        return self.reader if self.pipes else None

    def wait(self, timeout: float | None = None) -> int:
        self.waits.append(timeout)
        if self.hangs and not self.killed:
            raise subprocess.TimeoutExpired("sim-mirror mcp", timeout or 0)
        return 0

    def kill(self) -> None:
        self.killed = True


class _Reader:
    """Reads what the fake server wrote, line by line, as a pipe does."""

    def __init__(self) -> None:
        self.buffer = io.StringIO()
        self.at = 0

    def readline(self) -> str:
        self.buffer.seek(self.at)
        line = self.buffer.readline()
        self.at = self.buffer.tell()
        self.buffer.seek(0, io.SEEK_END)
        return line


def test_percentiles_are_nearest_rank() -> None:
    values = [5.0, 1.0, 4.0, 2.0, 3.0]
    assert (percentile(values, 50), percentile(values, 95), percentile(values, 0)) == (3.0, 5.0, 1.0)
    assert percentile([7.0], 95) == 7.0
    with pytest.raises(ValueError):
        percentile([], 50)


def test_a_run_through_the_relay_measures_each_tool_offered(capsys: pytest.CaptureFixture[str]) -> None:
    process = FakeProcess(noise=True)
    out, err = io.StringIO(), io.StringIO()
    spawned: list[str | None] = []

    def spawn(scope: str | None) -> Any:
        spawned.append(scope)
        return process

    assert main(["--scope", "demo", "--runs", "3"], spawn=spawn, out=out, err=err) == 0
    assert spawned == ["demo"] and process.waits == [tool_budget.EXIT_WAIT_S] and process.writer.closed
    lines = out.getvalue().splitlines()
    assert lines[0].startswith("| Call | Tool | Bytes | Tokens (est.)")
    assert len(lines) == 2 + len(PROBES)
    screenshot = next(line for line in lines if "400 px" in line)
    assert f"| {-(-400 * 868 // 750)} |" in screenshot and "| 0/3 |" in screenshot
    assert "| 100 |" in next(line for line in lines if "snapshot, full" in line)


def test_json_rows_and_only_offered_tools() -> None:
    out = io.StringIO()
    assert (
        main(["--json", "--runs", "1"], spawn=lambda scope: FakeProcess(["sim_device"]), out=out, err=io.StringIO())
        == 0
    )
    rows = json.loads(out.getvalue())
    assert [row["tool"] for row in rows] == ["sim_device"] and rows[0]["runs"] == 1


@pytest.mark.parametrize(
    ("process", "said"),
    [
        (FakeProcess([]), "offers none of the tools measured"),
        (FakeProcess(pipes=False), "started without pipes"),
    ],
)
def test_a_server_that_cannot_be_measured_says_why(process: FakeProcess, said: str) -> None:
    err = io.StringIO()
    assert main([], spawn=lambda scope: process, out=io.StringIO(), err=err) == 1
    assert said in err.getvalue()


def test_runs_must_be_counted_from_one() -> None:
    err = io.StringIO()
    assert main(["--runs", "0"], spawn=lambda scope: FakeProcess(), out=io.StringIO(), err=err) == 2
    assert "--runs is at least 1" in err.getvalue()


def test_a_server_that_hangs_on_exit_is_killed() -> None:
    process = FakeProcess(hangs=True)
    assert main(["--runs", "1"], spawn=lambda scope: process, out=io.StringIO(), err=io.StringIO()) == 0
    assert process.killed and process.waits == [tool_budget.EXIT_WAIT_S, None]


def test_the_client_reports_a_closed_server_and_an_error_reply() -> None:
    silent = McpClient(io.StringIO(), io.StringIO())
    with pytest.raises(BenchmarkError, match="closed its output before answering initialize"):
        silent.initialize()
    replies = io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"message": "nope"}}) + "\n")
    with pytest.raises(BenchmarkError, match="tools/list failed: nope"):
        McpClient(io.StringIO(), replies).tools()
    odd = io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 1, "result": ["not", "an", "object"]}) + "\n")
    assert McpClient(io.StringIO(), odd).request("ping", {}) == {}


def test_errors_are_counted_and_times_come_from_the_clock() -> None:
    ticks = iter([0.0, 0.25, 1.0, 1.5])
    reply = {"jsonrpc": "2.0", "id": 0, "result": {"content": [{"type": "text", "text": "off"}], "isError": True}}
    lines = "".join(json.dumps({**reply, "id": n}) + "\n" for n in (1, 2))
    client = McpClient(io.StringIO(), io.StringIO(lines), clock=lambda: next(ticks))
    [sample] = measure(client, [Probe("info", "sim_device", {"action": "info"})], runs=2)
    assert sample.errors == 2 and sample.seconds == [0.25, 0.5]
    row = summarize(sample)
    assert row == Row("info", "sim_device", row.size_bytes, 1, 250.0, 500.0, 2, 2)
    assert "| info | `sim_device` |" in table([row])
    assert summarize(Samples(PROBES[0], [0.001], [10], [3])).p95_ms == 1.0


def test_the_real_spawn_runs_sim_mirror_mcp_with_this_interpreter(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[list[str]] = []

    def popen(argv: list[str], **kwargs: Any) -> str:
        started.append(argv)
        return "process"

    monkeypatch.setattr(tool_budget.subprocess, "Popen", popen)
    assert tool_budget.spawn_mcp("demo") == "process"
    tool_budget.spawn_mcp(None)
    assert started[0][1:] == ["-m", "sim_mirror", "mcp", "--scope", "demo"]
    assert started[1][1:] == ["-m", "sim_mirror", "mcp"]
