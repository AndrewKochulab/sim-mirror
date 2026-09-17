# Comparison

Other tools that let agents or people see and drive iOS simulators, as their own READMEs, docs and release pages
describe them. **Last checked 2026-09-15.** Stars and release dates change; "not stated" means the sources checked do not
say, and "unverified" means they mention it without saying how it works. Corrections are welcome as a pull request with
a source.

## At a glance

| Tool | License | Interface | Live viewer in a browser | Reads the UI tree | Input | Platforms | Latest (date) |
|---|---|---|---|---|---|---|---|
| **SimMirror** (this project) | Apache-2.0 | MCP server, CLI, local daemon, web component, Python library, Claude Code plugin | Yes: H.264 through WebCodecs, or JPEG | Yes: the accessibility tree, read by its own native helper or idb_companion, as snapshots with refs and diffs | Yes: its own native helper, or idb_companion; view-only fallback over simctl | iOS simulator | v1.0.0 |
| [XcodeBuildMCP](https://github.com/getsentry/XcodeBuildMCP) | MIT | MCP server and CLI | Not stated | Yes: AXe `describe-ui` | Yes: AXe | UI automation on iOS simulators; build and run also for devices and macOS | v2.7.0 (2026-07-23) |
| [ios-simulator-mcp](https://github.com/joshuayoes/ios-simulator-mcp) | MIT | MCP server | No (screenshot and video tools) | Yes: IDB | Yes: IDB | iOS simulator | v2.1.0 (2026-08-13) |
| [ios-simulator-skill](https://github.com/conorluddy/ios-simulator-skill) | MIT | Claude Code skill (Python scripts) | Not stated | Yes: `idb` | Yes: `idb` | iOS simulator | v1.5.0 (2026-09-12) |
| [AXe](https://github.com/cameroncooke/AXe) | MIT | CLI | No | Yes: `axe describe-ui` | Yes: HID, from an IDB fork | iOS simulator (Xcode 26 and 27) | v1.8.0 (2026-07-20) |
| [serve-sim](https://github.com/EvanBacon/serve-sim) | Apache-2.0 | CLI, web preview, dev-server middleware, agent skill | Yes: MJPEG or H.264 | Not stated | Yes: gestures and typing; mechanism not stated | Apple simulators | npm 0.1.46; last commit 2026-09-12 |
| [Baguette](https://github.com/tddworks/baguette) | Apache-2.0 | CLI, web UI, HTTP and WebSocket API | Yes: MJPEG or H.264, up to 60 fps | Yes: private accessibility framework | Yes: SimulatorKit HID | iOS simulator | v0.1.97 (2026-08-31) |
| [sim-use](https://github.com/lycorp-jp/sim-use) | Apache-2.0 | CLI and agent skill | Partial: the UI tree drawn in a browser, not video | Yes: accessibility APIs; Android AccessibilityService | Yes: Simulator HID; Android bridge | iOS simulator, Android, physical iOS (experimental) | v0.14.0 (2026-08-27) |
| [agent-device](https://github.com/callstack/agent-device) | MIT | CLI, MCP server, Node.js API | Not stated | Yes: snapshots with refs and diffs | Yes: XCTest on iOS, ADB on Android | iOS, Android and HarmonyOS simulators, emulators and devices; more | v0.21.2 (2026-09-14) |
| [mobile-mcp](https://github.com/mobile-next/mobile-mcp) | Apache-2.0 | MCP server | Not stated | Yes: accessibility snapshots | Yes: through `mobilecli` | iOS simulator and device, Android emulator and device | 1.0.4 (2026-09-13) |
| [Appium MCP](https://github.com/appium/appium-mcp) | Apache-2.0 | MCP server | Partial: static viewers, not a live stream | Yes: page source through Appium drivers | Yes: Appium drivers (WebDriverAgent on iOS) | iOS and Android, simulators and devices | v1.94.0 (2026-09-10) |
| [Maestro MCP](https://github.com/mobile-dev-inc/Maestro) | Apache-2.0 | MCP server in the Maestro CLI | Yes: Maestro Viewer; streaming unverified | Yes: view hierarchy as JSON | Yes: Maestro flows | iOS simulator, Android emulator and device, web | CLI 2.10.0 (2026-08-31) |
| [agent-simulator](https://github.com/jasonkneen/agent-simulator) | AGPL-3.0 | Browser UI and MCP server | Yes: MJPEG | Yes: AXe | Yes: AXe | iOS simulator | Last commit 2026-04-28 |
| [SimDeck](https://github.com/NativeScript/SimDeck) | MIT | CLI, browser UI, VS Code extension, agent skill, test API | Yes: H.264 | Yes: private accessibility APIs; Android UIAutomator | Yes; mechanism not stated | iOS simulator, Android emulator | v0.2.0 (2026-09-11) |
| [SimView](https://github.com/ToolingTools/SimView) | Apache-2.0 | CLI, MCP server and MCP App, plugins, TS client | Yes: H.264 | Yes: XCTest runner, accessibility fallback; Android UIAutomator | Yes: SimulatorKit HID; ADB | iOS simulator, Android emulator and devices | v0.4.4 (2026-09-09) |
| [simcast](https://github.com/simcast-dev/simcast) | MIT | macOS app and a web control page (needs Supabase and LiveKit accounts) | Yes: WebRTC | Partial: tap by accessibility label | Yes: AXe | iOS simulator | v1.0.4 (2026-04-19) |
| [Claude Code Desktop's iOS Simulator pane](https://code.claude.com/docs/en/desktop-ios-simulator) | Part of a commercial app | Desktop app pane (public beta) | In the app, not a browser: H.264 or JPEG | Mechanism unverified | Person and Claude; mechanism unverified | iOS simulator; Xcode 26 | Claude Desktop 1.24012.0 or later |
| [Xcode 27 `mcpbridge`](https://developer.apple.com/documentation/xcode/giving-external-agents-access-to-xcode) | Ships with Xcode | MCP server (`xcrun mcpbridge`) | No: a screenshot per capture | Yes: UI hierarchy as text, per capture | Yes: taps, swipes, buttons and typing, each waiting for the screen to settle | Xcode projects, simulators and devices | Xcode 27.0 (27A266a) |
| [RocketSim](https://www.rocketsim.app/docs) | Proprietary | macOS app, CLI, agent skill | Yes: Browser Preview (beta); streaming unverified | Yes: `rocketsim elements`; mechanism unverified | Yes: `rocketsim interact` and the browser | iOS simulator | App Store 16.4.7 |

## Notes

- **XcodeBuildMCP** is foremost a build, run, test and debug server; its UI tools wrap AXe. Device tools need code
  signing.
- **ios-simulator-skill** warns that on Xcode 27, taps are silently dropped with idb-companion older than 1.5.1.
- **AXe** uses Device Hub on Xcode 27 and does not need Simulator.app.
- **serve-sim** captures through `simctl io`; on Xcode 27, its keyboard input needs Device Hub in front.
- **agent-device** scopes sessions to a git worktree, with device claims and replayable scripts.
- **Maestro MCP** now ships inside the Maestro CLI; the earlier standalone `maestro-mcp` repository is archived.
- **Claude Code Desktop's pane** runs one simulator per session, needs consent per device, sends screenshots to
  Anthropic, and is not available from the CLI, where Claude reaches a simulator through computer use.
- **Xcode 27's `mcpbridge`** has device-interaction tools beside its build, preview, string catalog and debugger
  tools. Measured on Xcode 27.0 (2026-09-16): a capture answered in 0.2 to 0.9 seconds with a hierarchy and a
  screenshot written to files, a tap in 3.3 seconds, a simulator is in one session at a time, and an agent is
  approved by opening a project first. SimMirror reads the hierarchy through it (the `mcpbridge` connector, and
  merged into idb's snapshots).
- **RocketSim**'s CLI talks to the running app; network control needs its Pro subscription.

## Where SimMirror sits

SimMirror's combination is a live viewer in any browser tab, iframe or web component; an agent cursor that shows each
gesture before it lands; snapshots with stable refs and diffs for agents; one set of MCP tools for any client; and an
embedding API for host applications -- all on your Mac, over loopback. It drives iOS simulators only today; real
devices and Android emulators are on the [roadmap](../ROADMAP.md).
