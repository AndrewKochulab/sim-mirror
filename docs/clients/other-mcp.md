# Any other MCP client

SimMirror is an MCP server over stdio. Any client that starts one and lists its tools can use it.

| Field | Value |
|---|---|
| Command | `uvx` (or `sim-mirror` if it is installed on `PATH`) |
| Arguments | `--from sim-mirror==1.2.0 sim-mirror mcp` (or just `mcp`) |
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

A client's own name, from `initialize`, is what viewers show beside its cursor.

## Tools

See the [tool reference](../reference/tools.md). Errors -- the simulator off, a connector that cannot touch the
screen, a ref no longer on screen -- come back as tool results that say what to do, never as protocol errors.

## A client that runs its own server process

A host application with its own server can give its agents SimMirror's tools through the same relay, pointed at its
own routes and credentials; see [Python host applications](../embedding/python-fastapi.md).
