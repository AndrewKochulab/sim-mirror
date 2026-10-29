# Performance measurements

## 2.4.0: 2026-10-29

These results measure tool calls through the MCP relay and local daemon, on Settings → General at the top of the
page, in portrait. They are observations on these devices and screens, not latency guarantees or a controlled
comparison with an older release.

- Library: SimMirror 2.4.0, source commit `c39f22af1bf5e0e3c8e19991408224c5cbaad188`; the benchmark's report and
  warmup options were added in the measurement PR. No library performance changes were made for this run.
- Simulator: iOS simulator, native connector, release helper built from this checkout.
- Physical device: iOS device over USB, WebDriverAgent and the live cable screen. The device health
  check confirmed element-tree reading and touch before the run.
- No browser viewer was connected during measurement.
- Each report uses one initial round, three warmup rounds, then 30 measured rounds. Each round calls device info,
  full snapshot, unchanged-screen diff, 400-pixel screenshot and 1200-pixel screenshot, in that order.

Public reports omit host and device models, OS/build versions, display geometry, names, addresses, identifiers,
local paths, scope names, signing details and exact capture timestamps. They contain numeric samples and generic
workload descriptions, without screenshots or screen text. This limits hardware-specific reproduction: rerunning
the procedure on another setup will not necessarily reproduce these timings.

The devices were running and the screen was prepared before the benchmark. The initial round is **not cold startup**:
it records the first call to each tool in a fresh MCP client, after device preparation. Startup, tool discovery and
preparation are outside the timed calls. Initial and warmup calls are excluded from the measured percentiles.

## Simulator through the native connector

| Call | Response bytes | Tokens (est.) | p50 ms | p95 ms | Errors |
|---|---:|---:|---:|---:|---:|
| Device info | 275 | 49 | 260.1 | 282.9 | 0/30 |
| Full snapshot | 561 | 112 | 74.3 | 83.3 | 0/30 |
| Unchanged-screen diff | 83 | 5 | 66.5 | 73.5 | 0/30 |
| Screenshot, 400 px | 55958 | 469 | 15.5 | 17.0 | 0/30 |
| Screenshot, 1200 px | 251484 | 4181 | 24.2 | 25.8 | 0/30 |

Initial-call durations, in the same order: 279.5, 69.9, 66.3, 52.1 and 26.4 ms. All initial and warmup calls also
succeeded. [Raw simulator report](performance/2026-10-29-native.json).

## Physical iOS device over USB

| Call | Response bytes | Tokens (est.) | p50 ms | p95 ms | Errors |
|---|---:|---:|---:|---:|---:|
| Device info | 282 | 47 | 11.4 | 12.8 | 0/30 |
| Full snapshot | 936 | 199 | 859.1 | 980.4 | 0/30 |
| Unchanged-screen diff | 83 | 5 | 817.9 | 859.3 | 0/30 |
| Screenshot, 400 px | 58110 | 469 | 31.4 | 37.3 | 0/30 |
| Screenshot, 1200 px | 255064 | 4181 | 29.5 | 33.4 | 0/30 |

Initial-call durations, in the same order: 13.2, 969.5, 802.2, 1005.2 and 31.1 ms. All initial and warmup calls also
succeeded. The first 400-pixel screenshot took about a second even though subsequent captures were much faster;
it is retained separately rather than hidden in the steady-state summary.
[Raw iPhone report](performance/2026-10-29-iphone.json).

The real iPhone and simulator show different versions of Settings and expose different element trees. Their full
snapshot sizes are therefore different workloads, not a measure of one connector's token efficiency relative to the
other. The unchanged-screen diff is 5 estimated tokens on both.

## Reading the numbers

Response bytes include the entire result serialized as JSON, including base64 for images. Bytes and estimated tokens
are medians; latency percentiles use nearest rank. Thirty calls give a more useful p95 than the historical five-call
run, whose p95 was simply its maximum, but they still describe a short run on one screen.

Token estimates use text characters divided by four and image pixels divided by 750, rounded up separately. They are
the project's comparison heuristic, **not model billing measurements**. They exclude tool definitions, prompts,
reasoning and conversation history. A snapshot's token savings do not imply that it is faster than a screenshot:
reading an accessibility tree and capturing an image are different operations. A small diff still requires reading
the screen to determine what changed.

These runs do not measure complete task time or task token usage, action batches, scrolling/settling, long-tree
recovery, reconnects, recording, streaming FPS, Android, `sim_snapshot max_elements`, or screenshot saving with
`return_image: false`. Recent reliability fixes can avoid repeated failures while screen checks and settling add
intentional waits. Neither effect is established by these read-only results.

## Historical baselines

The README previously showed this 2026-09-16 run: Settings → General on an iOS simulator using **idb**, five calls per
tool. Its raw samples and exact library commit were not retained in this report.

| Call | Response bytes | Tokens (est.) | p50 ms | p95 ms |
|---|---:|---:|---:|---:|
| Device info | 265 | 46 | 2.9 | 3.6 |
| Full snapshot | 561 | 112 | 64.8 | 76.4 |
| Unchanged-screen diff | 83 | 5 | 63.2 | 64.8 |
| Screenshot, 400 px | 62222 | 469 | 8.2 | 8.9 |
| Screenshot, 1200 px | 276472 | 4181 | 20.4 | 21.9 |

The old idb run and new native run differ in connector, environment, sample count and code. In particular, the new
device-info latency is substantially higher; these measurements do not isolate its cause. There is no claimed
across-the-board speedup. Snapshot token estimates on this simulator screen remain 112 for full and 5 for unchanged.

Other dated observations remain useful within their original scope:

- [Connector-only timings](connectors.md#native): 2026-09-17, outside the daemon and MCP relay.
- [Cable-screen and touch timings](real-devices.md#the-cable-screen): an older physical-device run; the screen table is
  dated 2026-09-29. A touch's own duration is not a complete `sim_act` call.
- [Recording sizes](recording.md): examples from September 29 and October 16, before the 2.4 duration/rendering fixes.
- [Android timings](android.md#measured): measurements added October 25, not rerun here.

## Reproduce

Follow [the token-budget example](../examples/token-budget/) to select a device and prepare its screen. Use a
dedicated scope and confirm the connector actually in use. On a real iPhone, keep USB connected, set up WebDriverAgent
and check `sim_device health` before preparing General. Leave the device untouched during collection.

```sh
uv run python benchmarks/tool_budget.py --scope demo --warmup 3 --runs 30 --report > tool-budget.json
```

The report retains the metrics for every measured call, plus initial and warmup summaries, without screen contents.
Keep detailed environment notes private; publish only generic workload metadata and the public source revision.
Verify the error counts, and run devices sequentially so their benchmarks do not compete for the host.
