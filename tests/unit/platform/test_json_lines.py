# SPDX-License-Identifier: Apache-2.0
"""A child spoken to in JSON lines: answers matched to requests, stops and silences said, and nothing left running."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from sim_mirror.platform import json_lines
from sim_mirror.platform.json_lines import JsonLines, LinesError, spawn_lines
from sim_mirror.testing.fakes import FakeLineProcess


class Echo:
    """A program that answers each request with what it was sent, unless told to keep quiet."""

    def __init__(self) -> None:
        self.exit_on_close = True
        self.quiet = False
        self.heard: list[dict[str, Any]] = []
        self.processes: list[FakeLineProcess] = []

    async def spawn(self, argv: Sequence[str], env: Mapping[str, str]) -> FakeLineProcess:
        process = FakeLineProcess(self, tuple(argv), dict(env))
        self.processes.append(process)
        return process

    def receive(self, process: FakeLineProcess, message: dict[str, Any]) -> None:
        self.heard.append(message)
        if "id" in message and not self.quiet:
            process.say({"id": message["id"], "echo": message["say"]})


async def started(echo: Echo, **options: Any) -> JsonLines:
    lines = JsonLines("helper", spawn=echo.spawn, **options)
    await lines.start(("/bin/helper", "--serve"), {"LANG": "C"})
    return lines


async def test_each_answer_is_matched_to_its_request_and_a_message_needs_none() -> None:
    echo = Echo()
    lines = await started(echo)
    assert lines.alive and echo.processes[0].argv == ("/bin/helper", "--serve")
    first, second = await asyncio.gather(
        lines.request({"say": "one"}, timeout=5, what="one"), lines.request({"say": "two"}, timeout=5, what="two")
    )
    await lines.send({"note": "no answer wanted"})
    assert (first["echo"], second["echo"]) == ("one", "two") and echo.heard[-1] == {"note": "no answer wanted"}
    await lines.close()
    assert not lines.alive and echo.processes[0].returncode == 1


async def test_a_request_not_answered_in_time_ends_the_program_and_says_who_did_not_answer() -> None:
    echo = Echo()
    echo.quiet = True
    lines = await started(echo)
    with pytest.raises(LinesError, match=r"\Ahelper did not answer reading within 0\.05 seconds\Z"):
        await lines.request({"say": "x"}, timeout=0.05, what="reading")
    assert not lines.alive
    quiet = Echo()
    quiet.quiet = True
    named = await started(quiet, answerer="Vision")
    with pytest.raises(LinesError, match=r"\AVision did not answer reading within 0\.05 seconds\Z"):
        await named.request({"say": "x"}, timeout=0.05, what="reading")


class Refused(Exception):
    pass


async def test_a_program_that_stops_says_what_it_said_last_as_the_callers_own_error() -> None:
    echo = Echo()
    lines = await started(echo, error=Refused)
    echo.quiet = True
    waiting = asyncio.ensure_future(lines.request({"say": "x"}, timeout=5, what="x"))
    await asyncio.sleep(0)
    echo.processes[0].stderr.feed_data(b"\n")
    echo.processes[0].finish(3, "fatal: no memory")
    with pytest.raises(Refused, match=r"\Ahelper stopped: fatal: no memory\Z"):
        await waiting
    assert not lines.alive
    with pytest.raises(Refused, match="helper stopped"):
        await lines.send({"say": "late"})
    await lines.close()
    with pytest.raises(Refused, match=r"\Ahelper stopped: fatal: no memory\Z"):
        await lines.send({"say": "closed"})
    quiet = await started(Echo())
    await quiet.close()
    with pytest.raises(LinesError, match=r"\Ahelper stopped\Z"):
        await quiet.send({"say": "closed"})


async def test_a_program_that_cannot_be_started_or_written_to_is_said() -> None:
    async def cannot(argv: Sequence[str], env: Mapping[str, str]) -> FakeLineProcess:
        raise PermissionError("not allowed")

    with pytest.raises(LinesError, match="helper could not be started: not allowed"):
        await JsonLines("helper", spawn=cannot).start(("/bin/helper",), {})
    echo = Echo()
    lines = await started(echo)
    echo.processes[0].stdin.closed = True
    with pytest.raises(LinesError, match="helper stopped"):
        await lines.send({"say": "x"})
    await lines.close()


async def test_lines_that_answer_nothing_are_ignored_and_one_too_long_stops_the_program() -> None:
    echo = Echo()
    lines = await started(echo)
    process = echo.processes[0]
    for noise in (b"not json\n", b"[1, 2]\n", b'{"id": "text"}\n', b'{"id": 999}\n', b'{"note": 1}\n'):
        process.stdout.feed_data(noise)
    assert (await lines.request({"say": "still here"}, timeout=5, what="x"))["echo"] == "still here"

    async def overrun() -> bytes:
        raise ValueError("Separator is not found, and chunk exceed the limit")

    process.stdout.readline = overrun  # type: ignore[method-assign]
    process.stdout.feed_data(b"{}\n")
    echo.quiet = True
    with pytest.raises(LinesError):
        await lines.request({"say": "x"}, timeout=0.2, what="x")
    await lines.close()


async def test_starting_again_ends_the_program_before_and_one_that_will_not_end_is_killed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(json_lines, "CLOSE_S", 0.01)
    echo = Echo()
    echo.exit_on_close = False
    lines = await started(echo)
    await lines.start(("/bin/helper",), {})
    assert echo.processes[0].killed and lines.alive and len(echo.processes) == 2
    await lines.close()
    await lines.close()
    assert echo.processes[1].killed


async def test_requests_already_answered_are_not_failed_when_the_program_stops() -> None:
    lines = JsonLines("helper")
    answered: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
    answered.set_result({"id": 1})
    waiting: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
    lines._pending.update({1: answered, 2: waiting})
    lines._fail_pending("stopped")
    assert answered.result() == {"id": 1}
    with pytest.raises(LinesError, match="stopped"):
        waiting.result()


async def test_the_real_spawn_starts_the_program_with_pipes_in_its_own_group() -> None:
    # `cat` stands in for a helper: it answers a line with the same line, and ends when stdin closes.
    process = await spawn_lines(["/bin/cat"], {"PATH": "/bin"})
    process.stdin.write(b'{"id": 1}\n')
    await process.stdin.drain()
    assert json.loads(await process.stdout.readline()) == {"id": 1}
    process.stdin.close()
    assert await process.wait() == 0
