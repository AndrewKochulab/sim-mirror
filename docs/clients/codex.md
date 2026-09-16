# OpenAI Codex

Add SimMirror to `~/.codex/config.toml`:

```toml
[mcp_servers.sim-mirror]
command = "uvx"
args = ["--from", "git+https://github.com/AndrewKochulab/sim-mirror@v0.2.0", "sim-mirror", "mcp"]
tool_timeout_sec = 900
```

- **Which project**: `sim-mirror mcp` uses the folder Codex starts in -- its scope's own simulator, and that folder as
  the only place installs and builds may reach. Append `"--scope", "my-app", "--root", "/path/to/my-app"` to `args`
  to pin one.
- **Timeouts**: `sim_build_run` and `sim_test` can take minutes when build tools are on; SimMirror bounds each tool
  itself, and a long build answers with a `build_id` to wait on.
- **Watch**: `sim-mirror open` in the same folder.

A copy of this configuration is in [examples/codex](../../examples/codex/).
