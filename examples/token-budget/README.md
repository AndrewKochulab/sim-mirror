# Token budget

What each tool's answer costs an agent, measured on your own screens with
[benchmarks/tool_budget.py](../../benchmarks/tool_budget.py).

```sh
sim-mirror open --scope demo     # start the device and put something on its screen
uv run python benchmarks/tool_budget.py --scope demo --runs 5
```

It starts `sim-mirror mcp` for the scope, speaks MCP to it over stdio as a client does, and calls each read-only tool
`--runs` times: device info, a full snapshot, a diff snapshot of the unchanged screen, and screenshots at 400 and 1200
pixels wide. Nothing is tapped or typed. For each call it prints a Markdown table row:

| Column | Meaning |
|---|---|
| Bytes | The answer as JSON, as it reaches the client (median) |
| Tokens (est.) | Text at about 4 characters a token, an image at about 750 pixels a token (median) |
| p50 ms, p95 ms | Latency through the relay and the daemon |
| Errors | Calls that answered with an error |

`--json` prints the same rows as JSON. The token counts are estimates -- each model counts its own way -- and are for
comparing answers with each other, not for billing.
