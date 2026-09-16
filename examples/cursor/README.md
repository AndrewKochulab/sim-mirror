# Cursor

Copy [.cursor/mcp.json](.cursor/mcp.json) into your app's folder (or merge its `sim-mirror` entry into an existing
one), and enable the server in Cursor's MCP settings.

- `sim-mirror mcp` gives the agent the simulator of the folder it starts in, and that folder as the only place builds
  and installs may reach. If the tools act on the wrong project, append `"--scope", "my-app", "--root",
  "/absolute/path/to/my-app"` to `args`.
- Watch and take over in a browser: `uvx --from git+https://github.com/AndrewKochulab/sim-mirror@v0.2.0 sim-mirror open`
  in the same folder.

The tools are the same for every client; see [claude-code-cli](../claude-code-cli/) for a first prompt to try.
