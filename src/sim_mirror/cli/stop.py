# SPDX-License-Identifier: Apache-2.0
"""``sim-mirror stop``: end a project's session on its device -- its viewers closed, its connector's companion stopped
-- as the viewer's Stop does; ``--shutdown`` shuts its simulator down too, and ``--daemon`` stops the daemon after it.

Only this scope is stopped: another project's device, agent and viewers are left as they are, and so is a real device,
which SimMirror never shuts down. ``--shutdown`` is refused while another scope shares the simulator -- shutting it
down would end their session too -- naming them, unless ``--force``; without it, only this scope lets go. Whatever
other scopes still hold the device afterwards are named, and so are the devices other scopes hold when this one held
none -- so "no device was running" never hides a device the daemon still drives.

``--device <udid>`` lets one device go for every scope using it instead, naming the scopes and the agents that held
it; the device keeps running.

``--daemon`` stops the daemon only while it serves no other scope -- no device, no agent's lease, no build -- and names
those it does, unless ``--force``; it is asked to stop as ``Ctrl-C`` would ask it, by the pid it wrote down, and waited
for. It is written down as stopped on purpose first (`lifecycle.mark_stopped`): a ``sim-mirror mcp`` still connected
answers its next tool call that SimMirror was stopped, and starts it again on the call after. Nothing is started only to
be stopped: with no daemon there is nothing to do.
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any
from urllib.parse import quote

from sim_mirror.cli.context import CliContext
from sim_mirror.cli.devices import SCOPE_HELP
from sim_mirror.daemon.lifecycle import clear_stopped, mark_stopped
from sim_mirror.mcp.launcher import NOTHING, OTHER, OURS, DaemonClient, DaemonUnavailable, impostor

#: How long a stopping daemon is waited for: its connections get 5 s to drain, then every device is let go.
STOP_WAIT_S = 20.0
STOP_POLL_S = 0.2
IN_USE = "/api/v1/admin/in-use"


def register(commands: Any) -> None:
    command = commands.add_parser("stop", help="stop a project's device session, and the daemon when asked")
    command.add_argument("--scope", help=SCOPE_HELP)
    command.add_argument(
        "--device",
        metavar="UDID",
        help="let this device go for every scope using it, instead of only this project's (it keeps running)",
    )
    command.add_argument(
        "--shutdown", action="store_true", help="shut its simulator or emulator down too (never a real device)"
    )
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
        return (
            f"{label}: stopped, and its {'emulator' if answer.get('kind') == 'android' else 'simulator'} is shut down"
        )
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


def sharing(entries: Sequence[Mapping[str, Any]], scope_id: str) -> list[str]:
    """The other scopes running the same device as this one, from the daemon's in-use list."""
    udid = next((entry.get("udid") for entry in entries if entry["id"] == scope_id), None)
    return [str(entry["id"]) for entry in entries if udid and entry["id"] != scope_id and entry.get("udid") == udid]


def held_by(entries: Sequence[Mapping[str, Any]], leaving: str) -> dict[str, list[Mapping[str, Any]]]:
    """Each device the daemon runs for a scope other than `leaving`, and the scopes running it, from its in-use list."""
    found: dict[str, list[Mapping[str, Any]]] = {}
    for entry in entries:
        if entry.get("udid") and entry["id"] != leaving:
            found.setdefault(str(entry["udid"]), []).append(entry)
    return found


def still_held(udid: str, holders: Sequence[Mapping[str, Any]], *, other: bool) -> str:
    """A device other scopes still hold, naming them, and how to let it go for all of them."""
    name = holders[0].get("device")
    count = f"{len(holders)} {'other ' if other else ''}scope{'' if len(holders) == 1 else 's'}"
    named = ", ".join(str(entry["id"]) + (" (an agent)" if entry.get("agent") else "") for entry in holders)
    return (
        f"{udid}{f' ({name})' if name else ''} is still held by {count}: {named}; use "
        f"`sim-mirror stop --device {udid}` to let it go for all of them, or --daemon"
    )


def let_go_lines(answer: Mapping[str, Any]) -> list[str]:
    """What letting a device go for every scope did, naming who held it."""
    udid = answer.get("udid")
    if not answer.get("stopped"):
        return [f"{udid}: SimMirror was not running it, so there was nothing to let go"]
    scopes = answer.get("scopes") or []
    if not scopes:
        return [f"{udid}: let go; no scope held it, and the device keeps running"]
    lines = [f"{udid}: let go for {len(scopes)} scope{'' if len(scopes) == 1 else 's'}; the device keeps running"]
    for entry in scopes:
        agents = entry.get("agents") or []
        lines.append(f"  {entry['id']}" + (f", held by {', '.join(agents)}" if agents else ""))
    return lines


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


def stop_scope(args: argparse.Namespace, ctx: CliContext, client: DaemonClient) -> int | None:
    """Stop the scope's session, saying what still holds its device -- or the devices other scopes hold, when it held
    none. An exit status when it was refused, else None."""
    scope = ctx.scope(args.scope)
    # Shut down for everyone sharing it, it is left held by nobody to be named.
    forced = args.shutdown and args.force
    entries: list[Mapping[str, Any]] = [] if forced else client.get(IN_USE)["scopes"]
    if args.shutdown and not args.force:
        shared = sharing(entries, scope.id)
        if shared:
            ctx.complain(
                f"sim-mirror: {scope.label}'s simulator is also used by {', '.join(shared)}, so it is not shut down: "
                "shutting it down would end their session too. Leave out --shutdown to stop only this one, or add "
                "--force"
            )
            return 1
    mine = next((entry.get("udid") for entry in entries if entry["id"] == scope.id), None)
    answer = client.admin("DELETE", f"/api/v1/scopes/{scope.id}" + ("?shutdown=true" if args.shutdown else ""))
    ctx.say(stopped_line(scope.label, answer, args.shutdown))
    held = held_by(entries, scope.id)
    if answer.get("stopped") and mine in held:
        ctx.say(still_held(str(mine), held[str(mine)], other=True))
    elif not answer.get("stopped"):
        for udid, holders in held.items():
            ctx.say(still_held(udid, holders, other=False))
    return None


def run(args: argparse.Namespace, ctx: CliContext) -> int:
    if args.device is not None and args.shutdown:
        ctx.complain("sim-mirror: --device lets a device go and leaves it running; it does not go with --shutdown")
        return 2
    scope = ctx.scope(args.scope)
    client = ctx.client()
    found = client.probe()
    if found == OTHER:
        raise DaemonUnavailable(impostor(client.url))
    if found == NOTHING:
        ctx.say("the SimMirror daemon is not running: nothing to stop")
        return 0
    if args.device is not None:
        answer = client.admin("DELETE", f"/api/v1/admin/devices/{quote(args.device, safe='')}")
        for line in let_go_lines(answer):
            ctx.say(line)
    else:
        refused = stop_scope(args, ctx, client)
        if refused is not None:
            return refused
    if not args.daemon:
        return 0
    if not args.force:
        others = [entry for entry in client.get(IN_USE)["scopes"] if entry["id"] != scope.id]
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
