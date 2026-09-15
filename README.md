# SimMirror

**Mirror and drive the iOS Simulator from Claude Code, CLI agents and the browser.**

A live viewer for the Simulator that runs in any browser tab or web page, an animated cursor that shows exactly what
your AI agent is tapping, and token-efficient UI snapshots so agents read the screen as compact text instead of
screenshots. Works with Claude Code, Codex, Cursor and any other MCP client.

> **Status: under active development.** The first public preview, `v0.1.0`, is being built on the
> [`feature/v0.1`](https://github.com/AndrewKochulab/sim-mirror/tree/feature/v0.1) branch. Installation instructions,
> documentation, examples and recordings land with it.

## Why SimMirror

- **See the Simulator anywhere.** A live H.264 or JPEG stream in a Chrome tab, an iframe or a `<sim-mirror>` web
  component, with full touch, keyboard and hardware-button control.
- **Watch what the agent does.** Every agent gesture is drawn by an on-screen cursor that leads the tap, so you can
  follow along and step in.
- **Spend fewer tokens.** Agents read accessibility snapshots with stable element refs and diffs between screens, and
  reach for a screenshot only when a question is visual.

## Roadmap

| Milestone | What it brings |
|---|---|
| **v0.1** (preview) | Live viewer, agent cursor, person and agent control, MCP tools, `sim-mirror doctor`, idb and simctl connectors, build and test as a preview |
| **v1.0** | Stable protocol and embedding API |
| **v1.1** | Build, run and test tools become stable |
| **v1.2** | Richer screen understanding, a native Swift helper, Xcode 27 `mcpbridge` connector, one shared daemon for many hosts |
| **v2.0** | Real iPhones: view, install and launch without signing; full control through WebDriverAgent |
| **Android** | Android emulator support with the same viewer, cursor and tools |
| **Homebrew** | `brew install andrewkochulab/tap/sim-mirror`, right after v1.0 |
| **Website & launch** | Landing page, video tutorials and guides |

See [ROADMAP.md](ROADMAP.md) for details.

## Contributing

Contributions are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) and the [Code of Conduct](CODE_OF_CONDUCT.md).
Report security issues privately as described in [SECURITY.md](SECURITY.md).

## License

[Apache-2.0](LICENSE). See [NOTICE](NOTICE) and [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).

SimMirror is an independent project, not affiliated with Apple or Anthropic. iOS, iPhone, Xcode and Simulator are
trademarks of Apple Inc.; Claude and Claude Code are trademarks of Anthropic PBC.
