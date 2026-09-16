# Claude Code in a terminal

Claude Code drives the simulator through SimMirror's MCP tools; you watch, and step in, in a browser tab.

## Install with the plugin

```
/plugin marketplace add AndrewKochulab/sim-mirror
/plugin install sim-mirror@sim-mirror
```

The plugin brings:

- the tools, from `sim-mirror mcp` run with uvx at the release's tag;
- a **skill** that teaches the snapshot-first loop -- read with `sim_snapshot`, act with `sim_act` batches and waits,
  screenshot only for what text cannot tell;
- **`/sim-mirror:open`**, which opens this project's viewer.

Its tools are named `mcp__plugin_sim-mirror_sim-mirror__sim_snapshot` and so on. To try a local copy of the plugin
instead, start Claude Code with `claude --plugin-dir plugins/sim-mirror`.

## Install without the plugin

```sh
claude mcp add sim-mirror -- uvx --from git+https://github.com/AndrewKochulab/sim-mirror@v0.1.1 sim-mirror mcp
```

`--scope project` (before the name) writes it to the project's `.mcp.json`, for everyone working on it. The tools are
named `mcp__sim-mirror__sim_snapshot` and so on.

## Which project

`sim-mirror mcp` gives the agent **the scope of the folder Claude Code runs in** -- its own simulator -- and that
folder as the only place `sim_app install` and the build tools may reach. To choose otherwise:

```sh
claude mcp add sim-mirror -- uvx --from git+https://github.com/AndrewKochulab/sim-mirror@v0.1.1 \
  sim-mirror mcp --scope my-app --root /Users/me/Projects/my-app
```

## Permissions

Claude Code asks before each new tool. To allow them all, add the server's rule to your permission settings:
`mcp__sim-mirror` (or `mcp__plugin_sim-mirror_sim-mirror` for the plugin).

## Watch it

```sh
sim-mirror open                  # or /sim-mirror:open with the plugin
```

The viewer shows the device live; every agent gesture is drawn as a cursor just before it lands
(`agent.cursor_lead_ms`, 250 ms by default, only while someone watches). You can use the device yourself at any time: an
agent waits for your hand to be still before its next gesture.

## A first prompt

> Open Settings → General → About and tell me which iOS version this simulator runs. Use sim_snapshot rather than
> screenshots.

## Build and test

`sim_build_run` and `sim_test` are a preview, off by default in a standalone install:

```sh
sim-mirror config set build.tools true
```

They build the project in the agent's root folder with xcodebuild for its simulator, install and launch it, and answer
with only what failed and where.

## Tips

- Ask for snapshots, not screenshots; see [Screen understanding](../screen-understanding.md) for why.
- A tool that says its connector cannot do something means idb_companion is missing or not found: run
  `sim-mirror doctor`.
- `sim-mirror tools` lists what this install offers.
