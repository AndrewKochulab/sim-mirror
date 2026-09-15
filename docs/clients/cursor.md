# Cursor

Add SimMirror to your project's `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "sim-mirror": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/AndrewKochulab/sim-mirror@v0.1.0", "sim-mirror", "mcp"]
    }
  }
}
```

and enable the server in Cursor's MCP settings.

- **Which project**: `sim-mirror mcp` uses the folder it starts in. If the tools act on the wrong project, append
  `"--scope", "my-app", "--root", "/absolute/path/to/my-app"` to `args`.
- **Watch**: `sim-mirror open` in the same folder.

A copy of this configuration is in [examples/cursor](../../examples/cursor/).
