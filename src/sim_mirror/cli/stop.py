# SPDX-License-Identifier: Apache-2.0
"""``sim-mirror stop``: end a project's session on its device -- its viewers closed, its connector's companion stopped
-- as the viewer's Stop does; ``--shutdown`` shuts its simulator down too, and ``--daemon`` stops the daemon after it.

Only this scope is stopped: another project's device, agent and viewers are left as they are, and so is a real device,
which SimMirror never shuts down. ``--shutdown`` is refused while another scope shares the simulator -- shutting it
down would end their session too -- naming them, unless ``--force``; without it, only this scope lets go.

``--daemon`` stops the daemon only while it serves no other scope -- no device, no agent's lease, no build -- and names
those it does, unless ``--force``; it is asked to stop as ``Ctrl-C`` would ask it, by the pid it wrote down, and waited
for. It is written down as stopped on purpose first (`lifecycle.mark_stopped`): a ``sim-mirror mcp`` still connected
answers its next tool call that SimMirror was stopped, and starts it again on the call after. Nothing is started only to
be stopped: with no daemon there is nothing to do.
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable, Mapping
from typing import Any

from sim_mirror.cli.context import CliContext
from sim_mirror.cli.devices import SCOPE_HELP
from sim_mirror.daemon.lifecycle import clear_stopped, mark_stopped
from sim_mirror.mcp.launcher import NOTHING, OTHER, OURS, DaemonClient, DaemonUnavailable, impostor

#: How long a stopping daemon is waited for: its connections get 5 s to drain, then every device is let go.
STOP_WAIT_S = 20.0
STOP_POLL_S = 0.2


def register(commands: Any) -> None:
    command = commands.add_parser("stop", help="stop a project's device session, and the daemon when asked")
    command.add_argument("--scope", help=SCOPE_HELP)
    command.add_argument("--shutdown", action="store_true", help="shut its simulator down too (never a real device)")
    command.add_argument("--daemon", action="store_true", help="stop the SimMirror daemon too, unless it serves others")
    command.add_argument(
        "--force", action="store_true", help="with --shutdown or --daemon: go ahead even while other scopes use it"
    )
    command.set_defaults(handler=run)


def stopped_line(label: str, answer: Mapping[str, Any], shutdown: bool) -> str:
    """What stopping the scope's device did, as a person reads it."""
    if not answer.get("stopped"):
        return f"{label}: no device was running"
    if answer.get("shut_down", shutdown):
        return f"{label}: stopped, and its simulator is shut down"
    if shutdown:
        return f"{label}: stopped; a real device is never shut down, so it stays on"
    return f"{label}: stopped; the device keeps running (--shutdown shuts a simulator down too)"


def serving(entry: Mapping[str, Any]) -> str:
    """One other scope the daemon serves, and with what."""
    what = [
        *((f"running {entry['device']}",) if entry.get("device") else ()),
        *(("an agent",) if entry.get("agent") else ()),
        *(("a build",) if entry.get("building") else ()),
    ]
    return f"{entry['id']} ({', '.join(what)})" if what else str(entry["id"])


def sharing(entries: list[Mapping[str, Any]], scope_id: str) -> list[str]:
    """The other scopes running the same device as this one, from the daemon's in-use list."""
    udid = next((entry.get("udid") for entry in entries if entry["id"] == scope_id), None)
    return [str(entry["id"]) for entry in entries if udid and entry["id"] != scope_id and entry.get("udid") == udid]


def wait_stopped(
    client: DaemonClient,
    *,
    wait_s: float = STOP_WAIT_S,
    poll_s: float = STOP_POLL_S,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Whether the daemon stopped answering within `wait_s`."""
    deadline = clock() + wait_s
    while client.probe() == OURS:
        if clock() >= deadline:
            return False
        sleep(poll_s)
    return True


def run(args: argparse.Namespace, ctx: CliContext) -> int:
    scope = ctx.scope(args.scope)
    client = ctx.client()
    found = client.probe()
    if found == OTHER:
        raise DaemonUnavailable(impostor(client.url))
    if found == NOTHING:
        ctx.say("the SimMirror daemon is not running: nothing to stop")
        return 0
    if args.shutdown and not args.force:
        shared = sharing(client.get("/api/v1/admin/in-use")["scopes"], scope.id)
        if shared:
            ctx.complain(
                f"sim-mirror: {scope.label}'s simulator is also used by {', '.join(shared)}, so it is not shut down: "
                "shutting it down would end their session too. Leave out --shutdown to stop only this one, or add "
                "--force"
            )
            return 1
    answer = client.admin("DELETE", f"/api/v1/scopes/{scope.id}" + ("?shutdown=true" if args.shutdown else ""))
    ctx.say(stopped_line(scope.label, answer, args.shutdown))
    if not args.daemon:
        return 0
    if not args.force:
        others = [entry for entry in client.get("/api/v1/admin/in-use")["scopes"] if entry["id"] != scope.id]
        if others:
            ctx.complain(
                "sim-mirror: the daemon still serves "
                + "; ".join(serving(entry) for entry in others)
                + ", so it keeps running: stop each with `sim-mirror stop --scope <id>`, or add --force"
            )
            return 1
    run_dir = ctx.state.run_dir()
    # Before the signal: a relay whose call finds the daemon gone must already know a person stopped it.
    mark_stopped(run_dir)
    if not ctx.retire_daemon():
        clear_stopped(run_dir)
        raise DaemonUnavailable(f"the daemon at {client.url} wrote down no pid to stop it by; stop it where it runs")
    if not wait_stopped(client):
        raise DaemonUnavailable(f"the SimMirror daemon did not stop within {STOP_WAIT_S:g}s")
    ctx.say("stopped the SimMirror daemon; the devices it did not shut down keep running")
    return 0
