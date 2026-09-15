# Examples

Each folder is one way to use SimMirror, small enough to copy.

| Example | What it shows |
|---|---|
| [claude-code-cli](claude-code-cli/) | Claude Code in a terminal: the plugin, or `claude mcp add`, and the viewer in a browser tab |
| [codex](codex/) | OpenAI Codex: `~/.codex/config.toml` |
| [cursor](cursor/) | Cursor: `.cursor/mcp.json` |
| [iframe](iframe/) | A web page showing the viewer in a frame, opened with a one-shot embed ticket its backend minted |
| [web-component](web-component/) | The `<sim-mirror>` element on a page, themed with CSS custom properties |
| [embed-host](embed-host/) | The small backend the iframe and web-component pages share: it keeps the token and mints tickets |
| [fastapi-embed](fastapi-embed/) | A Python host application mounting SimMirror's routers under its own paths, with its own sign-in |
| [custom-connector](custom-connector/) | A connector of your own, installed through the `sim_mirror.connectors` entry point, with contract tests |
| [token-budget](token-budget/) | Measuring what each tool's answer costs an agent |

Every example assumes a Mac with Xcode and a booted-or-bootable simulator, and `sim-mirror doctor` passing. The
examples are tested with SimMirror's own suite (`tests/examples/`), without starting a simulator.
