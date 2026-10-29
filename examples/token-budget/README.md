# Token budget

What each tool's answer costs an agent, measured on your own screens with
[benchmarks/tool_budget.py](../../benchmarks/tool_budget.py).

```sh
sim-mirror open --scope demo     # start the device and put something on its screen
uv run python benchmarks/tool_budget.py --scope demo --runs 5
```

For a result to publish, prepare a named screen and connector, then keep a separate initial round, three warmup
rounds and 30 measured rounds in one MCP session:

```sh
uv run python benchmarks/tool_budget.py --scope demo --warmup 3 --runs 30 --report > tool-budget.json
```

`--report` writes JSON with the package version, the initial round, warmup summaries, measured summaries and every
measured duration, byte count and token estimate. It stores no screenshot pixels or screen text. Keep detailed host
and device information in private notes; publish only the public source revision, generic device class, connector
and workload. Do not attach device-info output, identifiers, account names, paths or screen contents. Check every row's `errors`
before publishing; a failed call's small error message is not a successful snapshot's token cost.

The initial round is each tool's first call in this MCP session, in the listed order. It is **not** a cold device or
daemon startup benchmark: the device may already be running and the screen may have been read during preparation.
Warmup rounds are excluded from the measured percentiles. `--warmup` defaults to zero; `--json` still prints only the
measured summary rows. The p95 uses nearest rank: with five calls it is the slowest call, which is why the published
refresh uses 30 calls per tool.

See [the dated performance report](../../docs/performance.md) for the current results and historical baselines.

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

These are response costs, excluding tool definitions, prompts, model reasoning and conversation history. They do not
measure tokens or elapsed time for completing a whole task. The benchmark does not tap, scroll, build, record, test
reconnection, or exercise `sim_screenshot save_to`, `return_image: false` or `sim_snapshot max_elements`.
