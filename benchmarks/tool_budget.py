# SPDX-License-Identifier: Apache-2.0
"""What each SimMirror tool's answer costs an agent, measured through the MCP relay a client uses.

    uv run python benchmarks/tool_budget.py --scope demo --runs 5

It starts ``sim-mirror mcp`` for a scope -- which starts the daemon when it is not running -- speaks MCP to it over
stdio as a client does, and calls each read-only tool `--runs` times on whatever the device shows. For each call it
prints the answer's size, its estimated tokens (`sim_mirror.perception.budget`: estimates, and said to be) and the
p50/p95 latency. Nothing is tapped or typed.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import IO, Any, Protocol

from sim_mirror.perception.budget import estimate

CLIENT_NAME = "sim-mirror benchmark"
MCP_PROTOCOL = "2025-06-18"
EXIT_WAIT_S = 10.0


@dataclass(frozen=True)
class Probe:
    label: str
    tool: str
    arguments: Mapping[str, Any]


#: The calls measured, none of which changes the screen. The diff follows a full snapshot: an unchanged screen.
PROBES: tuple[Probe, ...] = (
    Probe("device info", "sim_device", {"action": "info"}),
    Probe("snapshot, full", "sim_snapshot", {"mode": "full"}),
    Probe("snapshot, diff of an unchanged screen", "sim_snapshot", {"mode": "diff"}),
    Probe("screenshot, 400 px wide", "sim_screenshot", {"width": 400}),
    Probe("screenshot, 1200 px wide", "sim_screenshot", {"width": 1200}),
)


class BenchmarkError(Exception):
    """The MCP server did not answer as a client needs."""


@dataclass
class Samples:
    probe: Probe
    seconds: list[float] = field(default_factory=list)
    sizes: list[int] = field(default_factory=list)
    tokens: list[int] = field(default_factory=list)
    errors: int = 0


@dataclass(frozen=True)
class Row:
    label: str
    tool: str
    size_bytes: int
    tokens: int
    p50_ms: float
    p95_ms: float
    errors: int
    runs: int


def percentile(values: Sequence[float], q: float) -> float:
    """The nearest-rank percentile: the smallest value that `q` percent of the values are at or below."""
    if not values:
        raise ValueError("a percentile of no values")
    ordered = sorted(values)
    return ordered[max(1, math.ceil(q / 100 * len(ordered))) - 1]


class McpClient:
    """A client's side of MCP over stdio: a request written as a line, answered by the line with its id."""

    def __init__(self, send: IO[str], receive: IO[str], clock: Callable[[], float] = time.perf_counter) -> None:
        self._send = send
        self._receive = receive
        self._clock = clock
        self._ids = 0

    def _write(self, message: Mapping[str, Any]) -> None:
        self._send.write(json.dumps(message) + "\n")
        self._send.flush()

    def request(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
        self._ids += 1
        self._write({"jsonrpc": "2.0", "id": self._ids, "method": method, "params": params})
        while True:
            line = self._receive.readline()
            if not line:
                raise BenchmarkError(f"the MCP server closed its output before answering {method}")
            reply = json.loads(line)
            if isinstance(reply, dict) and reply.get("id") == self._ids:
                break
        if "error" in reply:
            raise BenchmarkError(f"{method} failed: {reply['error'].get('message')}")
        result = reply.get("result")
        return result if isinstance(result, dict) else {}

    def initialize(self) -> dict[str, Any]:
        info = {"name": CLIENT_NAME, "version": "1"}
        result = self.request("initialize", {"protocolVersion": MCP_PROTOCOL, "capabilities": {}, "clientInfo": info})
        self._write({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result

    def tools(self) -> list[str]:
        return [str(tool.get("name")) for tool in self.request("tools/list", {}).get("tools", [])]

    def call(self, tool: str, arguments: Mapping[str, Any]) -> tuple[dict[str, Any], float]:
        started = self._clock()
        result = self.request("tools/call", {"name": tool, "arguments": dict(arguments)})
        return result, self._clock() - started


def measure(client: McpClient, probes: Iterable[Probe], runs: int) -> list[Samples]:
    """Each probe `runs` times, in rounds, so every diff snapshot follows a full one."""
    samples = [Samples(probe) for probe in probes]
    for _ in range(runs):
        for sample in samples:
            result, seconds = client.call(sample.probe.tool, sample.probe.arguments)
            budget = estimate(result)
            sample.seconds.append(seconds)
            sample.sizes.append(budget.size_bytes)
            sample.tokens.append(budget.tokens)
            sample.errors += 1 if result.get("isError") else 0
    return samples


def summarize(sample: Samples) -> Row:
    return Row(
        label=sample.probe.label,
        tool=sample.probe.tool,
        size_bytes=round(percentile(sample.sizes, 50)),
        tokens=round(percentile(sample.tokens, 50)),
        p50_ms=round(percentile(sample.seconds, 50) * 1000, 1),
        p95_ms=round(percentile(sample.seconds, 95) * 1000, 1),
        errors=sample.errors,
        runs=len(sample.seconds),
    )


def table(rows: Iterable[Row]) -> str:
    lines = [
        "| Call | Tool | Bytes | Tokens (est.) | p50 ms | p95 ms | Errors |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row.label} | `{row.tool}` | {row.size_bytes} | {row.tokens} | {row.p50_ms} | {row.p95_ms} | "
            f"{row.errors}/{row.runs} |"
        )
    return "\n".join(lines)


class McpProcess(Protocol):
    @property
    def stdin(self) -> IO[str] | None: ...

    @property
    def stdout(self) -> IO[str] | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def kill(self) -> None: ...


def spawn_mcp(scope: str | None) -> McpProcess:
    """``sim-mirror mcp`` for the scope, with this interpreter, talking over pipes."""
    argv = [sys.executable, "-m", "sim_mirror", "mcp", *(["--scope", scope] if scope else [])]
    return subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)


def stop(process: McpProcess) -> None:
    """Close the client's side, which ends the server; kill it when it does not end."""
    if process.stdin is not None:
        process.stdin.close()
    try:
        process.wait(timeout=EXIT_WAIT_S)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def parser() -> argparse.ArgumentParser:
    made = argparse.ArgumentParser(description="Measure what each SimMirror tool's answer costs an agent.")
    made.add_argument("--scope", help="the scope to measure, instead of this folder's project")
    made.add_argument("--runs", type=int, default=5, help="calls of each tool (default 5)")
    made.add_argument("--json", action="store_true", help="print the rows as JSON instead of a Markdown table")
    return made


def main(
    argv: Sequence[str] | None = None,
    *,
    spawn: Callable[[str | None], McpProcess] = spawn_mcp,
    out: IO[str] | None = None,
    err: IO[str] | None = None,
) -> int:
    args = parser().parse_args(argv)
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    if args.runs < 1:
        print("--runs is at least 1", file=err)
        return 2
    process = spawn(args.scope)
    try:
        if process.stdin is None or process.stdout is None:
            raise BenchmarkError("the MCP server was started without pipes")
        client = McpClient(process.stdin, process.stdout)
        client.initialize()
        offered = set(client.tools())
        probes = [probe for probe in PROBES if probe.tool in offered]
        if not probes:
            raise BenchmarkError("the MCP server offers none of the tools measured; is the simulator switched on here?")
        rows = [summarize(sample) for sample in measure(client, probes, args.runs)]
    except BenchmarkError as exc:
        print(exc, file=err)
        return 1
    finally:
        stop(process)
    print(json.dumps([row.__dict__ for row in rows], indent=2) if args.json else table(rows), file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
