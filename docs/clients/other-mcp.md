# Any other MCP client

SimMirror is an MCP server over stdio. Any client that starts one and lists its tools can use it.

| Field | Value |
|---|---|
| Command | `uvx` (or `sim-mirror` if it is installed on `PATH`) |
| Arguments | `--from sim-mirror==2.3.0 sim-mirror mcp` (or just `mcp`) |
| Optional arguments | `--scope NAME` for a scope other than the folder's; `--root DIR`, repeatable, for the folders installs and builds may reach |
| Environment | None needed |
| Transport | stdio, newline-delimited JSON-RPC |

## What happens when it starts

1. It checks whether SimMirror's daemon answers on `127.0.0.1` and starts one in the background when it does not
   (waiting up to 10 seconds).
2. It reads the admin token -- it runs as you -- and mints an **agent token** for its scope, passed to the relay in its
   environment and revoked when the client goes.
3. It answers MCP: `initialize` (with the instructions agents are given), `tools/list`, `tools/call` and `ping`.
4. While the client runs, it renews a lease on the scope's device every 30 seconds, so the device is not stopped as
   idle between tool calls.

A client's own name, from `initialize`, is what viewers show beside its cursor. A client that offers MCP **roots** is
asked for them once it is initialized, again on `notifications/roots/list_changed`, and again before each tool call
-- which waits up to two seconds for the answer, and a client that let it wait so long is not asked before a call
again: `sim_build_run` and `sim_test`
build the folder it works in -- such as a git worktree of the project -- when that is one the token may build
([Git worktrees](claude-code-cli.md#git-worktrees)).

## When the daemon stops

The client can stay connected. A tool call that finds nothing listening starts the daemon again, as the first launch
does, and is sent once more with the same token; after `sim-mirror stop --daemon` the first such call only says so
([troubleshooting](../troubleshooting.md#simmirror-was-stopped-by-sim-mirror-stop---daemon)). A daemon started again
on another port -- `server.port` changed meanwhile -- is found there, from `daemon.json` in the run folder or else the
config, and the calls go there once it has proved it is your daemon. Two clients bringing it back at once start one.

## Tools

See the [tool reference](../reference/tools.md). Errors -- the simulator off, a connector that cannot touch the
screen, a ref no longer on screen -- come back as tool results that say what to do, never as protocol errors.

## A client that runs its own server process

A host application with its own server can give its agents SimMirror's tools through the same relay, pointed at its
own routes and credentials; see [Python host applications](../embedding/python-fastapi.md).

## Running the relay yourself

A host that [shares the daemon](../embedding/shared-daemon.md) runs the relay itself (`python -I relay.py`, the
command `relay_command` or `DaemonHost.agent` gives) rather than `sim-mirror mcp`. That relay cannot start a daemon.
When the daemon is gone, a call answers in one line that nothing listens there any more and how to start it again:
`sim-mirror serve --detach`, then call again.

Give it the daemon's run folder -- `--run-dir ~/.sim-mirror/run`, or `SIM_MIRROR_RUN_DIR` when that is set;
`relay_command(run_dir=...)` and `DaemonHost.agent(..., run_dir=...)` add it -- and a call that finds nothing
listening first reads where the daemon listens now from `daemon.json` there, and goes to that port. It believes the
file only when you own it and the folder, nobody else may write to either, and the process it names is still yours
and running; it takes only the port from it, so calls stay on `127.0.0.1` with their own path. While `sim-mirror mcp`
is starting a daemon from that folder, the relay waits for it before it reads. The path is no secret, so it goes in
argv; the token stays in the environment.
