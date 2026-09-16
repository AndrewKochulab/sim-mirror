# SPDX-License-Identifier: Apache-2.0
"""Talking to Xcode's tools: ``xcrun mcpbridge``, an MCP server on stdin and stdout, one per client.

A `BridgeClient` starts the bridge with the scope's Xcode (``DEVELOPER_DIR``, `platform.developer_dir`), says hello
once, and calls tools, each answer matched to its call by id. What was measured on Xcode 27.0 (2026-09-16):

* the bridge reaches Xcode's tool service -- ``Xcode Service.app``, which runs without a window and is started when
  it is not running (about 2 seconds) -- and never opens Xcode itself;
* a call Xcode refuses answers a result with ``isError`` and its reason in the text, often as JSON with ``data``; a
  call it carries out answers ``structuredContent``;
* the bridge ends when its stdin closes.

Xcode 26.6 has an mcpbridge too, but it reaches only an Xcode that is open -- it stops with "no running Xcode processes
found" otherwise -- and SimMirror never opens Xcode. Its tools were not measured, so only Xcode 27 or later counts
(`find_bridge`).

Nothing here decides what to call; `reader.BridgeReader` does.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Protocol

from sim_mirror._version import __version__
from sim_mirror.connectors.base import ConnectorError
from sim_mirror.platform.developer_dir import developer_env
from sim_mirror.platform.process import kill_and_reap
from sim_mirror.platform.xcode import xcode_version
from sim_mirror.platform.xcrun import XcrunRunner, run_xcrun, xcrun_binary

#: The first Xcode whose bridge SimMirror uses: it reaches Xcode's tools without Xcode open, and can read a device.
XCODE_MAJOR = 27
#: The MCP version SimMirror speaks, as Xcode 27.0's bridge answers it.
PROTOCOL_VERSION = "2025-06-18"
#: The longest line an answer may be. A capture's answer is a few hundred bytes; the tool list is about 60 KB.
LINE_MAX = 1024 * 1024
#: How long saying hello may take: the first one starts Xcode's tool service.
HELLO_TIMEOUT_S = 30.0
#: How many of the bridge's last lines on stderr are kept, to say why it stopped.
STDERR_LINES = 5
#: How long a stopped bridge's last words on stderr are waited for.
STDERR_WAIT_S = 0.5
#: How long the bridge is given to end once its stdin is closed.
CLOSE_S = 2.0
#: How much of a refusal's text is passed on.
SAID_MAX = 400


class BridgeError(ConnectorError):
    """The bridge could not be started, stopped, or did not answer."""


class BridgeRefused(BridgeError):
    """Xcode answered a call with an error: `text` is its reason, as Xcode said it."""

    def __init__(self, tool: str, text: str) -> None:
        super().__init__(f"Xcode refused {tool}: {text}")
        self.tool = tool
        self.text = text


class BridgeProcess(Protocol):
    """The part of an `asyncio.subprocess.Process` a client uses."""

    stdin: Any
    stdout: Any
    stderr: Any

    @property
    def returncode(self) -> int | None: ...

    def kill(self) -> None: ...

    async def wait(self) -> int: ...


Spawn = Callable[[Sequence[str], Mapping[str, str]], Awaitable[BridgeProcess]]


async def spawn_bridge(argv: Sequence[str], env: Mapping[str, str]) -> BridgeProcess:
    """Start the bridge with pipes, in a process group of its own."""
    return await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=dict(env),
        limit=LINE_MAX,
        start_new_session=True,
    )


async def find_bridge(developer_dir: str, xcrun: XcrunRunner = run_xcrun) -> str | None:
    """Where the given Xcode keeps an mcpbridge SimMirror can use, or None: an Xcode before 27, or one without it."""
    found = await xcrun("--find", "mcpbridge", timeout=10.0, developer_dir=developer_dir)
    path = found.out.strip()
    if not (found.ok and path):
        return None
    major = re.match(r"Xcode (\d+)", await xcode_version(developer_dir, xcrun) or "")
    return path if major and int(major[1]) >= XCODE_MAJOR else None


def said(result: Mapping[str, Any]) -> str:
    """What Xcode said in a result: its ``data`` when the text is JSON that has one, else the text."""
    content = result.get("content")
    first = content[0] if isinstance(content, list) and content and isinstance(content[0], dict) else {}
    text = str(first.get("text") or "")
    with contextlib.suppress(ValueError):
        parsed = json.loads(text)
        if isinstance(parsed, dict) and parsed.get("data"):
            text = str(parsed["data"])
    return text[:SAID_MAX] or "no reason was given"


class BridgeClient:
    """One running ``xcrun mcpbridge``, started on the first call and ended by `close`."""

    def __init__(self, developer_dir: str = "", *, spawn: Spawn = spawn_bridge, client_name: str = "SimMirror") -> None:
        self.developer_dir = developer_dir
        self._spawn = spawn
        self._client_name = client_name
        self._process: BridgeProcess | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._next_id = 1
        self._stderr: deque[str] = deque(maxlen=STDERR_LINES)
        self._tasks: list[asyncio.Task[None]] = []
        self._starting = asyncio.Lock()
        self._ready = False

    @property
    def alive(self) -> bool:
        return self._process is not None and self._process.returncode is None and not self._stopped()

    def _stopped(self) -> bool:
        return bool(self._tasks) and self._tasks[0].done()

    async def call(self, tool: str, arguments: Mapping[str, Any], *, timeout: float) -> dict[str, Any]:
        """Call one of Xcode's tools, answering what it carried out; raises `BridgeRefused` when Xcode says no."""
        await self._start()
        answer = await self._request("tools/call", {"name": tool, "arguments": dict(arguments)}, timeout, tool)
        result = answer.get("result")
        if not isinstance(result, dict):
            error = answer.get("error")
            message = error.get("message") if isinstance(error, dict) else None
            raise BridgeRefused(tool, str(message or "the bridge answered without a result")[:SAID_MAX])
        if result.get("isError"):
            raise BridgeRefused(tool, said(result))
        structured = result.get("structuredContent")
        if not isinstance(structured, dict):
            raise BridgeRefused(tool, f"the answer had nothing SimMirror can read: {said(result)}")
        return structured

    async def close(self) -> None:
        """End the bridge: its stdin closed, then killed if it does not go. Closing twice is closing once."""
        process, self._process = self._process, None
        self._ready = False
        if process is not None:
            with contextlib.suppress(OSError, RuntimeError):
                process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), timeout=CLOSE_S)
            except (asyncio.TimeoutError, TimeoutError):
                await kill_and_reap(process)
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks = []
        self._fail_pending("the bridge was closed")

    async def _start(self) -> None:
        async with self._starting:
            if self._ready and self.alive:
                return
            await self.close()
            xcrun = xcrun_binary()
            if xcrun is None:
                raise BridgeError("Xcode command-line tools are not installed (no xcrun)")
            try:
                self._process = await self._spawn((xcrun, "mcpbridge"), developer_env(self.developer_dir))
            except OSError as exc:
                raise BridgeError(f"mcpbridge could not be started: {exc}") from exc
            self._stderr.clear()
            self._tasks = [asyncio.create_task(self._read_answers()), asyncio.create_task(self._read_stderr())]
            hello = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": self._client_name, "version": __version__},
            }
            answer = await self._request("initialize", hello, HELLO_TIMEOUT_S, "hello")
            if not isinstance(answer.get("result"), dict):
                raise BridgeError(f"mcpbridge did not accept SimMirror's hello: {answer.get('error')}")
            await self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            self._ready = True

    async def _send(self, message: Mapping[str, Any]) -> None:
        process = self._process
        if process is None:
            raise BridgeError(self._why_stopped())
        try:
            process.stdin.write(json.dumps(message).encode() + b"\n")
            await process.stdin.drain()
        except (OSError, RuntimeError) as exc:
            raise BridgeError(self._why_stopped()) from exc

    async def _request(self, method: str, params: Mapping[str, Any], timeout: float, what: str) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        waiting: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = waiting
        try:
            await self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": dict(params)})
            return await asyncio.wait_for(asyncio.shield(waiting), timeout=timeout)
        except (asyncio.TimeoutError, TimeoutError):
            # An answer this late is no use, and a bridge that stopped answering is not trusted with the next call.
            # The call is let go of first, so closing does not fail it again where nobody hears.
            self._pending.pop(request_id, None)
            await self.close()
            raise BridgeError(f"Xcode's tools did not answer {what} within {timeout:g} seconds") from None
        finally:
            self._pending.pop(request_id, None)

    async def _read_answers(self) -> None:
        process = self._process
        assert process is not None
        try:
            while line := await process.stdout.readline():
                with contextlib.suppress(ValueError):
                    message = json.loads(line)
                    answers = message.get("id") if isinstance(message, dict) else None
                    waiting = self._pending.get(answers) if isinstance(answers, int) else None
                    if waiting is not None and not waiting.done():
                        waiting.set_result(message)
        except (ValueError, asyncio.LimitOverrunError, OSError):
            # A line longer than LINE_MAX: nothing after it can be matched up, so the bridge counts as stopped.
            pass
        # What the bridge said on its way out is usually on stderr, which may still be being read.
        await asyncio.wait(self._tasks[1:], timeout=STDERR_WAIT_S)
        self._fail_pending(self._why_stopped())

    async def _read_stderr(self) -> None:
        process = self._process
        assert process is not None
        with contextlib.suppress(ValueError, asyncio.LimitOverrunError, OSError):
            while line := await process.stderr.readline():
                text = line.decode(errors="replace").strip()
                if text:
                    self._stderr.append(text)

    def _why_stopped(self) -> str:
        last = self._stderr[-1] if self._stderr else ""
        return f"mcpbridge stopped{': ' + last if last else ''}"

    def _fail_pending(self, why: str) -> None:
        for waiting in self._pending.values():
            if not waiting.done():
                waiting.set_exception(BridgeError(why))
