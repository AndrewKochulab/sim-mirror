# SimMirror documentation

## Start

- [Getting started](getting-started.md) -- from nothing to an agent tapping through an app, in a few minutes
- [Installation](installation.md) -- requirements, installing and updating, where files live
- [Troubleshooting](troubleshooting.md) and [the doctor](doctor.md)

## Use it from a client

- [Claude Code in a terminal](clients/claude-code-cli.md)
- [OpenAI Codex](clients/codex.md)
- [Cursor](clients/cursor.md)
- [Any other MCP client](clients/other-mcp.md)

## Change its settings

- [The settings panel](settings.md) -- every setting from the viewer, and a code at the terminal for the ones a page
  cannot change alone

## Put the viewer in your own app

- [An iframe](embedding/iframe.md)
- [The `<sim-mirror>` web component and the viewer library](embedding/web-component.md)
- [A Python host application](embedding/python-fastapi.md)

## How it works

- [Screen understanding](screen-understanding.md) -- snapshots, refs, diffs, waits, and what each costs
- [Connectors](connectors.md) -- how SimMirror reaches a device, and what each can do
- [Security](security.md) -- the threat model and what keeps the daemon to itself
- [Architecture](architecture.md) -- the parts, the seams and the rules between them
- [Compatibility](compatibility.md) -- where SimMirror is expected to work, and where it was checked
- [Stability](stability.md) -- what the tools, the protocol and the embedding API promise not to break
- [Comparison](comparison.md) -- other tools in this space, factually

## Reference

- [Agent tools](reference/tools.md)
- [Configuration](reference/configuration.md)
- [Command line](reference/cli.md)
- [Screen protocol](reference/protocol.md)

## Contribute

- [Contributing](../CONTRIBUTING.md) and [the guide for AI coding agents](../AGENTS.md)
- [Writing a connector](contributing/connector-guide.md)
- [Roadmap](roadmap.md)

The reference pages and the compatibility table are generated from the code (`make generate`); edit their sources, not
the pages.
