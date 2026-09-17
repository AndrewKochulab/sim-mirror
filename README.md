<!-- mcp-name: io.github.AndrewKochulab/sim-mirror -->

# SimMirror

**Mirror and drive the iOS Simulator from Claude Code, CLI agents and the browser.**

[![CI](https://github.com/AndrewKochulab/sim-mirror/actions/workflows/ci.yml/badge.svg)](https://github.com/AndrewKochulab/sim-mirror/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/AndrewKochulab/sim-mirror?include_prereleases&sort=semver)](https://github.com/AndrewKochulab/sim-mirror/releases)

![A Claude Code session tapping through Settings on an iPhone 17 Pro simulator in the viewer, its cursor named
and drawn before each tap](docs/media/hero.gif)

A live iOS Simulator in any browser tab or web page, an animated cursor that shows exactly what your AI agent is about
to tap, and token-efficient UI snapshots so agents read the screen as compact text instead of screenshots. Works with
Claude Code, Codex, Cursor and any other MCP client.

> **Status: 1.0.** The agent tools, the viewer, the protocol and the embedding API are stable, and a check holds every
> 1.x release to [what SimMirror promises not to break](docs/stability.md).

## Why SimMirror

- **See the simulator anywhere.** A live H.264 or JPEG stream in a Chrome tab, an iframe or a `<sim-mirror>` web
  component, with touch, scrolling, typing, hardware buttons and light and dark mode.
- **Watch what the agent does.** Every agent gesture is announced before it lands, and the viewer's cursor glides to
  the spot first -- so you can follow along, and step in: an agent waits for your hand to be still.
- **Spend fewer tokens.** Agents read accessibility snapshots with stable element refs and diffs between screens, act
  in batches with waits, and reach for a screenshot only when a question is visual.

## Install

**Claude Code**, with the plugin (tools, a skill that teaches snapshot-first use, and `/sim-mirror:open`):

```
/plugin marketplace add AndrewKochulab/sim-mirror
/plugin install sim-mirror@sim-mirror
```

**Any MCP client**, as a stdio server:

```sh
claude mcp add sim-mirror -- uvx --from sim-mirror==1.0.0 sim-mirror mcp
```

Configurations for [Codex](docs/clients/codex.md), [Cursor](docs/clients/cursor.md) and
[other clients](docs/clients/other-mcp.md).

**The command**, for `sim-mirror open`, `doctor` and the rest:

```sh
uv tool install sim-mirror==1.0.0
# or
brew install andrewkochulab/tap/sim-mirror
```

**The viewer library**, for your own pages: `npm install @andrewkochulab/sim-mirror`
([npm](https://www.npmjs.com/package/@andrewkochulab/sim-mirror)). SimMirror itself is
[on PyPI](https://pypi.org/project/sim-mirror/) and in a [Homebrew tap](https://github.com/AndrewKochulab/homebrew-tap).

**Requirements:** an Apple Silicon Mac, Xcode 26 or later, and [uv](https://docs.astral.sh/uv/) or Homebrew. Nothing
else: SimMirror drives a simulator with its own helper, which comes in the package. Details in
[Installation](docs/installation.md).

## Quickstart

```sh
sim-mirror doctor     # check this Mac, ending with a real tap on a simulator
sim-mirror open       # this project's simulator, live in a browser tab
```

Then ask your agent:

> Open Settings → General → About and tell me which iOS version this simulator runs. Use sim_snapshot rather than
> screenshots.

[Getting started](docs/getting-started.md) explains what happens along the way.

## Features

**Live viewer.** H.264 through WebCodecs where the page can decode it, JPEG otherwise, negotiated per connection.
Touch, drag, scroll, typing and paste, Home and Lock, light and dark, and a device picker. It reconnects by itself
after a blip and says plainly when the simulator is off.

**Agent cursor.** Every agent gesture -- tap, long press, swipe, drag, typing -- reaches the viewer before it lands, and
the viewer's own pointer glides there first (`agent.cursor_lead_ms`). Never the Mac's pointer.

**Screen understanding.** A snapshot is a few lines -- `e2 button "General" (201,319)` -- with refs that stay with their
elements across scrolls, a digest, and diffs. `sim_act` plays up to 20 steps in one call and waits for text, its
absence, or the screen to settle. See [Screen understanding](docs/screen-understanding.md).

**Connectors.** SimMirror's own native helper gives full control with nothing to install; idb_companion still can,
and the simctl connector mirrors the screen view-only when neither is there, and says why. `auto` uses the fastest that
works and falls back when one fails. More connectors plug in through an entry point. See
[Connectors](docs/connectors.md).

**Embedding.** An [iframe](docs/embedding/iframe.md) with a one-shot ticket, the
[`<sim-mirror>` element](docs/embedding/web-component.md) themed with CSS custom properties, or SimMirror
[inside a Python application](docs/embedding/python-fastapi.md) with its own sign-in.

**Settings panel.** `sim-mirror open --settings` puts every setting a click away in the viewer, a tab per section,
saved for one project or all of them and applied before it says so. Settings that decide what runs or who may reach
the daemon wait for a code from `sim-mirror settings confirm`. See [The settings panel](docs/settings.md).

**Doctor.** Checks Xcode, its Simulator frameworks, runtimes, the native helper and idb_companion, Device Hub and the
desktop session, then proves a tap reaches a simulator. See [the doctor](docs/doctor.md).

**Security.** Loopback only, a Host allowlist, exact Origin checks, CORS and framing only for origins you list, hashed
scoped tokens, and one-shot codes in URL fragments. See [Security](docs/security.md).

**Build and test.** `sim_build_run` and `sim_test` build with xcodebuild for the agent's simulator, install and
launch, and answer with only what failed and where -- each failing test named so it can be run again alone, and on
another simulator when asked. Off by default, since they run commands: `sim-mirror config set build.tools true`.

## Measure the token cost

`benchmarks/tool_budget.py` calls each read-only tool through the same MCP relay a client uses and prints each answer's
bytes, estimated tokens and latency on your own screens; see [the token budget example](examples/token-budget/). On
an iPhone 17 Pro simulator (iOS 26.5, idb connector) showing Settings → General, 5 calls each (2026-09-16):

| Call | Tool | Bytes | Tokens (est.) | p50 ms | p95 ms |
|---|---|---:|---:|---:|---:|
| device info | `sim_device` | 265 | 46 | 2.9 | 3.6 |
| snapshot, full | `sim_snapshot` | 561 | 112 | 64.8 | 76.4 |
| snapshot, diff of an unchanged screen | `sim_snapshot` | 83 | 5 | 63.2 | 64.8 |
| screenshot, 400 px wide | `sim_screenshot` | 62222 | 469 | 8.2 | 8.9 |
| screenshot, 1200 px wide | `sim_screenshot` | 276472 | 4181 | 20.4 | 21.9 |

Bytes are the whole answer, an image's base64 included; tokens are estimates (text at about 4 characters a token,
an image at about 750 pixels a token).

## Compatibility

| | Expected to work |
|---|---|
| macOS | 26 (15 with Xcode 16.4, view-only) |
| Xcode | 26 and 27 |
| Connectors | native helper (full control), idb_companion 1.5 (full control), simctl (view-only) |
| Clients | Claude Code, Codex, Cursor, any stdio MCP client |
| Browsers | Chrome, Safari, Firefox, Edge |

Rows checked on real hardware are marked verified, with the date, in [Compatibility](docs/compatibility.md).

From 1.0 the tools, the protocol, the embedding API, the viewer package, settings and the command line are held to
[what SimMirror promises not to break](docs/stability.md): a 1.x release adds, and never breaks. Connectors are the
exception, and pin a minor version.

## Documentation

[Everything](docs/README.md): getting started, clients, embedding, screen understanding, connectors, security,
architecture, and the reference for [tools](docs/reference/tools.md), [configuration](docs/reference/configuration.md),
the [command line](docs/reference/cli.md) and the [protocol](docs/reference/protocol.md).

## Roadmap

| Milestone | What it brings |
|---|---|
| **v0.1** (preview) | Live viewer, agent cursor, person and agent control, MCP tools, `sim-mirror doctor`, idb and simctl connectors, build and test as a preview |
| **v0.2** | Build, run and test tools, a settings panel, Xcode 27's UI hierarchy through `mcpbridge`, one shared daemon for many hosts |
| **v1.0** (now) | Stable protocol and embedding API, held by a check; PyPI, npm, Homebrew and the MCP Registry; a cursor that stays while the agent works |
| **v1.2** | A native Swift helper connector, an OCR and vision fallback reader, an optional in-app debug SDK |
| **v2.0** | Real iPhones: view, install and launch without signing; full control through WebDriverAgent |
| **Android** | Android emulator support with the same viewer, cursor and tools |
| **Website & launch** | Landing page, video tutorials and guides |

See [ROADMAP.md](ROADMAP.md) and the [milestones](https://github.com/AndrewKochulab/sim-mirror/milestones). How
SimMirror relates to other tools is in [Comparison](docs/comparison.md).

## Contributing

Contributions are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) and the [Code of Conduct](CODE_OF_CONDUCT.md); AI
coding agents read [AGENTS.md](AGENTS.md). Report security issues privately as described in [SECURITY.md](SECURITY.md).
Questions go to [Discussions](https://github.com/AndrewKochulab/sim-mirror/discussions).

## License

[Apache-2.0](LICENSE). See [NOTICE](NOTICE) and [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).

SimMirror is an independent project, not affiliated with Apple or Anthropic. iOS, iPhone, Xcode and Simulator are
trademarks of Apple Inc.; Claude and Claude Code are trademarks of Anthropic PBC.
