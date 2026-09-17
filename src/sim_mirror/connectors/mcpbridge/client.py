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
from collections.abc import Mapping
from typing import Any

from sim_mirror._version import __version__
from sim_mirror.connectors.base import ConnectorError
from sim_mirror.platform.developer_dir import developer_env
from sim_mirror.platform.json_lines import JsonLines, Spawn, spawn_lines
from sim_mirror.platform.xcode import xcode_version
from sim_mirror.platform.xcrun import XcrunRunner, run_xcrun, xcrun_binary

#: The first Xcode whose bridge SimMirror uses: it reaches Xcode's tools without Xcode open, and can read a device.
XCODE_MAJOR = 27
#: The MCP version SimMirror speaks, as Xcode 27.0's bridge answers it.
PROTOCOL_VERSION = "2025-06-18"
#: How long saying hello may take: the first one starts Xcode's tool service.
HELLO_TIMEOUT_S = 30.0
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
    """One running ``xcrun mcpbridge`` (`platform.json_lines`), started on the first call and ended by `close`."""

    def __init__(self, developer_dir: str = "", *, spawn: Spawn = spawn_lines, client_name: str = "SimMirror") -> None:
        self.developer_dir = developer_dir
        self._client_name = client_name
        self._lines = JsonLines("mcpbridge", spawn=spawn, error=BridgeError, answerer="Xcode's tools")
        self._starting = asyncio.Lock()
        self._ready = False

    @property
    def alive(self) -> bool:
        return self._lines.alive

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
        self._ready = False
        await self._lines.close()

    async def _start(self) -> None:
        async with self._starting:
            if self._ready and self.alive:
                return
            await self.close()
            xcrun = xcrun_binary()
            if xcrun is None:
                raise BridgeError("Xcode command-line tools are not installed (no xcrun)")
            await self._lines.start((xcrun, "mcpbridge"), developer_env(self.developer_dir))
            hello = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": self._client_name, "version": __version__},
            }
            answer = await self._request("initialize", hello, HELLO_TIMEOUT_S, "hello")
            if not isinstance(answer.get("result"), dict):
                raise BridgeError(f"mcpbridge did not accept SimMirror's hello: {answer.get('error')}")
            await self._lines.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            self._ready = True

    async def _request(self, method: str, params: Mapping[str, Any], timeout: float, what: str) -> dict[str, Any]:
        message = {"jsonrpc": "2.0", "method": method, "params": dict(params)}
        return await self._lines.request(message, timeout=timeout, what=what)
