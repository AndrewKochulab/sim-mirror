<!-- mcp-name: io.github.AndrewKochulab/sim-mirror -->

# SimMirror

**Mirror and drive the iOS Simulator from Claude Code, CLI agents and the browser.**

[![CI](https://github.com/AndrewKochulab/sim-mirror/actions/workflows/ci.yml/badge.svg)](https://github.com/AndrewKochulab/sim-mirror/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/AndrewKochulab/sim-mirror?include_prereleases&sort=semver)](https://github.com/AndrewKochulab/sim-mirror/releases)

A live iOS Simulator in any browser tab or web page, an animated cursor that shows exactly what your AI agent is about
to tap, and token-efficient UI snapshots so agents read the screen as compact text instead of screenshots. Works with
Claude Code, Codex, Cursor and any other MCP client.

> **Status: first public preview (v0.1).** The tools, the viewer and the protocol work today; the embedding API and
> protocol become stable with v1.0.

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
claude mcp add sim-mirror -- uvx --from git+https://github.com/AndrewKochulab/sim-mirror@v0.1.0 sim-mirror mcp
```

Configurations for [Codex](docs/clients/codex.md), [Cursor](docs/clients/cursor.md) and
[other clients](docs/clients/other-mcp.md).

**The command**, for `sim-mirror open`, `doctor` and the rest:

```sh
uv tool install git+https://github.com/AndrewKochulab/sim-mirror@v0.1.0
```

**The viewer library**, for your own pages: the npm package `@andrewkochulab/sim-mirror`, attached to each
[release](https://github.com/AndrewKochulab/sim-mirror/releases). Homebrew and PyPI come with v1.0.

**Requirements:** an Apple Silicon Mac, Xcode 26 or later, [uv](https://docs.astral.sh/uv/), and for touching the
screen, idb_companion (`brew install facebook/fb/idb-companion`). Details in [Installation](docs/installation.md).

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

**Connectors.** idb_companion gives full control; without it the simctl connector still mirrors the screen, view-only,
and says why. More connectors plug in through an entry point. See [Connectors](docs/connectors.md).

**Embedding.** An [iframe](docs/embedding/iframe.md) with a one-shot ticket, the
[`<sim-mirror>` element](docs/embedding/web-component.md) themed with CSS custom properties, or SimMirror
[inside a Python application](docs/embedding/python-fastapi.md) with its own sign-in.

**Doctor.** Checks Xcode, its Simulator frameworks, runtimes, idb_companion, Device Hub and the desktop session, then
proves a tap reaches a simulator. See [the doctor](docs/doctor.md).

**Security.** Loopback only, a Host allowlist, exact Origin checks, CORS and framing only for origins you list, hashed
scoped tokens, and one-shot codes in URL fragments. See [Security](docs/security.md).

**Build and test (preview).** `sim_build_run` and `sim_test` build with xcodebuild for the agent's simulator, install
and launch, and answer with only what failed and where. Off by default: `sim-mirror config set build.tools true`.

## Measure the token cost

`benchmarks/tool_budget.py` calls each read-only tool through the same MCP relay a client uses and prints each answer's
bytes, estimated tokens and latency on your own screens; see [the token budget example](examples/token-budget/).

## Compatibility

| | Expected to work |
|---|---|
| macOS | 26 (15 with Xcode 16.4, view-only) |
| Xcode | 26 and 27 |
| Connectors | idb_companion 1.5 (full control), simctl (view-only) |
| Clients | Claude Code, Codex, Cursor, any stdio MCP client |
| Browsers | Chrome, Safari, Firefox, Edge |

Rows checked on real hardware are marked verified, with the date, in [Compatibility](docs/compatibility.md).

## Documentation

[Everything](docs/README.md): getting started, clients, embedding, screen understanding, connectors, security,
architecture, and the reference for [tools](docs/reference/tools.md), [configuration](docs/reference/configuration.md),
the [command line](docs/reference/cli.md) and the [protocol](docs/reference/protocol.md).

## Roadmap

| Milestone | What it brings |
|---|---|
| **v0.1** (preview) | Live viewer, agent cursor, person and agent control, MCP tools, `sim-mirror doctor`, idb and simctl connectors, build and test as a preview |
| **v1.0** | Stable protocol and embedding API, PyPI and npm packages, the MCP registry |
| **v1.1** | Build, run and test tools become stable |
| **v1.2** | Richer screen understanding, a native Swift helper, Xcode 27 `mcpbridge` connector, one shared daemon for many hosts |
| **v2.0** | Real iPhones: view, install and launch without signing; full control through WebDriverAgent |
| **Android** | Android emulator support with the same viewer, cursor and tools |
| **Homebrew** | `brew install andrewkochulab/tap/sim-mirror`, right after v1.0 |
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
