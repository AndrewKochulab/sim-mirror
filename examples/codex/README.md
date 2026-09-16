# OpenAI Codex

Add [config.toml](config.toml)'s table to `~/.codex/config.toml`, then start Codex in your app's folder.

- `sim-mirror mcp` gives the agent the simulator of the folder it starts in, and that folder as the only place builds
  and installs may reach. The commented `args` pin a project by name and path instead.
- `tool_timeout_sec` is raised because `sim_build_run` and `sim_test` can take minutes (they are there only when build
  tools are switched on).
- Watch and take over in a browser: `uvx --from git+https://github.com/AndrewKochulab/sim-mirror@v0.2.0 sim-mirror open`
  in the same folder.

The tools are the same for every client; see [claude-code-cli](../claude-code-cli/) for a first prompt to try.
