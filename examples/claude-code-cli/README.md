# Claude Code in a terminal

Claude Code drives the simulator through SimMirror's MCP tools; you watch, and take over, in a browser tab.

## With the plugin

The plugin adds the tools, a skill that teaches the snapshot-first way to use them, and `/sim-mirror:open`.

```
/plugin marketplace add AndrewKochulab/sim-mirror
/plugin install sim-mirror@sim-mirror
```

Its tools are named `mcp__plugin_sim-mirror_sim-mirror__sim_snapshot` and so on.

## Without the plugin

```sh
claude mcp add sim-mirror -- uvx --from sim-mirror==0.2.0 sim-mirror mcp
```

Add `--scope project` before the name to write it to the project's `.mcp.json` for everyone working on it. Its tools
are named `mcp__sim-mirror__sim_snapshot` and so on; allow them all without a prompt with `mcp__sim-mirror` in your
permission settings.

`sim-mirror mcp` starts SimMirror's local server when it is not running, and gives the agent this folder's own
simulator (its scope) and this folder as the only place builds and installs may reach. Pass `--scope NAME` or
`--root DIR` after `mcp` to choose otherwise.

## Watch it

```sh
uvx --from sim-mirror==0.2.0 sim-mirror open
```

opens this folder's viewer in your browser. Every gesture the agent makes is drawn there as a cursor just before it
lands, and you can tap, scroll and type into the device yourself at any time.

## Try it

> Open Settings → General → About and tell me which iOS version this simulator runs. Use sim_snapshot rather than
> screenshots.

A snapshot-first run reads the answer from a few hundred tokens of text; the same task done with screenshots costs
several thousand. See [token-budget](../token-budget/) to measure it on your own screens.

## Requirements

An Apple Silicon Mac with Xcode 26 or later, [uv](https://docs.astral.sh/uv/), and for touching the screen,
`brew install facebook/fb/idb-companion`. Without idb_companion the viewer still shows the screen, view-only. Run
`sim-mirror doctor` to see what this Mac has.
