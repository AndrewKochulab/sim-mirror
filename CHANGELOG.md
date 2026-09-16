# Changelog

All notable changes to SimMirror are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **A settings panel in the viewer.** `sim-mirror open --settings` puts every setting a gear away, a tab per section
  of config.toml, saved for one project or every project, checked whole and applied before it says so. It shows
  where each value comes from and when a change takes effect, and will not write a value an environment variable or
  the command line sets. A plain `sim-mirror open` page reads settings and changes none; a framed page never sees
  them. See [The settings panel](docs/settings.md).
  - **Sensitive settings wait for a person at the terminal.** A page's change to what SimMirror runs or who may
    reach it (`connectors.idb.companion_path`, `device.developer_dir`, `build.tools`, `server.*`, `security.*`) is
    held until **`sim-mirror settings confirm`** shows it and a code that confirms exactly that change, once.
  - The shapes are in the protocol (`settings.schema.json`); a host mounts `create_settings_router` with its own
    `SettingsStore`, `SettingsAuthenticator` and `Confirmations`, all new on `sim_mirror.api` and preview until
    1.0 -- or mounts nothing and changes nothing.
- The configuration reference says, for every setting, when a change takes effect, whether a scope may have its own
  value, and whether it is sensitive.

- `sim_test` takes **`retries`**, giving a failing test that many more goes (`-retry-tests-on-failure`), and a run
  now reports what the retries revealed:
  - a test that **failed and then passed** is named as **flaky**, with the attempt it passed on. This is the one
    worth having: a flaky test is counted among the *passed* and appears nowhere in `testFailures`, so a run that
    only went green on the second go was, until now, indistinguishable from one that went green;
  - a failure that was retried says how many attempts it had, so "failed" is not read as "failed once".
  Both are read from `Repetition` nodes, whose shape is a fixture taken from a real run of the sample app on Xcode
  26.6 (`xcresult-test-*-retries.json`) rather than from the documentation.

**Not added, and why:** attachments. They appear in neither `xcresulttool get test-results summary` nor `tests` —
checked on that same bundle, with a test that keeps one — so each would cost another `xcresulttool` call per test,
to hand an agent a file it cannot open. If something wants them, it should ask for them by name.

- `sim-mirror doctor` says which Xcode SimMirror's programs run with and what named it (`device.developer_dir`,
  `DEVELOPER_DIR` or `xcode-select`), which Xcode the rest of the Mac uses when that is another, and — in a new
  `running companions` check — which Xcode each companion already running runs with.
- Xcode 27 is verified in the [compatibility table](docs/compatibility.md): Xcode 27.0 (27A266a) with iOS 27.0, beside
  Xcode 26.6, chosen both ways. Typing text does not reach an iOS 27.0 device yet — iOS 27 refuses the paste it is sent
  as, silently ([#27](https://github.com/AndrewKochulab/sim-mirror/issues/27)); touches, keys, the screen, the element
  tree and the agent tools work. [Troubleshooting](docs/troubleshooting.md#typing-does-nothing-on-ios-27) says so.

### Fixed

- **idb_companion runs with the scope's Xcode.** `device.developer_dir` reached simctl and xcodebuild but not the
  companion, which used whatever `xcode-select` named — so a device booted by one Xcode was shown and touched through
  another's SimulatorKit. Its pid file now names that Xcode on a second line, which the previous release still reads.
- **A running device follows a change of Xcode**, the way it already followed a change of connector: its screens are
  told it is restarting and come back on the new one.
- **The doctor's `xcode` check reads an inherited `DEVELOPER_DIR`**, as every program SimMirror starts does.
- **The doctor's `device hub` check sees Xcode 27's Device Hub.** It looked for a process named `Device Hub`; Xcode 27.0
  runs it as `DeviceHub`, so the check said "not open" while it was. Measured with it open: a device booted then still
  took taps, so the warning now says a simulator Device Hub has taken over *can* ignore them.
- **The doctor's tap waits for a new device's screen to be readable.** On a device's very first start it failed with
  "No translation object returned for simulator" — measured on Xcode 26.6 and 27.0 alike — and passed when run again.
- **Escape closes the device picker**, and no key, text, scroll or touch reaches the device while it is open: Escape
  used to go to the device (Safari) or do nothing (Chrome). The picker's rows take the arrow keys, Home and End.
- A viewer that left while its device restarted could leave a stream stop behind that failed with `KeyError: 'h264'`
  in the log fifteen seconds later.

### Changed

- **`sim-mirror config set --scope` refuses `server.*` and `security.*`.** The daemon reads those for itself alone,
  so a scope's table never changed them; setting one there used to succeed and do nothing.
- `sim-mirror config list` names where each value comes from -- the variable, the scope's table, the command line --
  instead of guessing, which called a command-line value "environment" and a file's value under `--scope` so too.
- config.toml is written under a lock, flushed to disk, and keeps its file mode; a new one is the owner's only.
- `/api/v1/auth/exchange` also answers the session's `kind`, and `TransportError` carries the server's body.
- `sim_mirror.testing.guards` refuses `xcode-select` too: a test that asks the Mac which Xcode is selected passes on
  one Mac only. A host's own suite using the guard will see such a test refused; `FakeXcodeSelect` stands in for it.

**Measured, not changed:** how `tools/list` explains a refused credential. In Claude Code 2.1.273 the reason reaches
the agent through `initialize`'s instructions when the refusal is there from the start, and through the call's own
refusal whenever a tool is called — so an agent can already tell a broken credential from a scope that is off. A refusal
that begins mid-session, announced with `list_changed`, reached the model in none of five `tools/list` shapes tried
(an empty list, a JSON-RPC error — asked for four times — a `_meta` field, a log notification, a placeholder tool), and
two of them made the model believe the server had disconnected. None is worth a change on that evidence.

## [0.1.1] - 2026-09-16

Found by embedding SimMirror in a second host application. Each of these is a place where a host had to implement or
reach for something it should have been handed.

### Changed

- **Breaking, for host applications.** `StateStore` no longer has `devices_file`, and `Runtime.build` now asks for
  `memory`. Where a scope's device is remembered belongs to the `DeviceMemory` that reads it, not to the store every
  host must implement: a host with somewhere better to keep it — a row in its database — used to have to answer a
  question about a file it never wrote. `JsonDeviceMemory` now takes the path itself, so a standalone install builds
  one with `JsonDeviceMemory(state.devices_file())` and a host with its own memory implements nothing about files at
  all. `Runtime.build` asks rather than defaulting, so a host is never given a JSON file it did not choose.

### Added

- `sim_test` takes a **`test_plan`**, one of the scheme's test plans, passed to xcodebuild as `-testPlan`. Naming one
  the scheme does not have answers with the ones it does — an agent cannot see the scheme, so the alternative is
  guessing again. A scheme with no test plans says to leave the argument out rather than failing obscurely, and
  leaving it out is what happened before: Xcode runs the scheme's default. A plan named on `sim_build_run` is
  refused, because a build runs no tests. The plans are read once per project and re-read when it changes, like its
  schemes. Both shapes `xcodebuild -showTestPlans -json` answers with are fixtures measured on Xcode 26.6 — a list,
  and `null` for a scheme that has none.
- A test run says how many tests **failed as expected** (`XCTExpectFailure`) when any did. Without it the passed,
  failed and skipped counts do not add up to the tests there were, and a passing run reads as having lost one. A run
  with none is unchanged.
- `sim_mirror.api` exports `InvalidScope`, which `Scope` raises and a host has to catch; `JsonDeviceMemory`, so a host
  need not write a `DeviceMemory` of its own; and `claims_dir`, which is how every host on one Mac sees the same device
  claims and so refuses each other's devices rather than fighting over one. All three were reachable only by importing
  past the public surface.
- [What SimMirror promises not to break](docs/stability.md): what the agent tools, the protocol and the Python
  embedding API cover, what they deliberately do not — the words a tool answers with, the CLI's output, configuration
  defaults, the viewer's markup — and how a deprecation runs. It says plainly that none of it binds while SimMirror is
  `0.x`, and takes effect at 1.0.

## [0.1.0] - 2026-09-16

### Added

- Live viewer: H.264 over WebCodecs with a JPEG fallback, in a browser tab, an iframe or the `<sim-mirror>` web
  component; touch, drag, scroll, typing, paste, hardware buttons, appearance and a device picker.
- Agent cursor: every agent gesture announced and drawn before it lands.
- MCP tools `sim_device`, `sim_snapshot`, `sim_screenshot`, `sim_act` and `sim_app`, through `sim-mirror mcp` and a
  standard-library relay.
- Screen understanding: accessibility snapshots with stable refs, digests and diffs; batched steps with waits for text
  and for the screen to settle; token estimates.
- Connectors: idb (full control) and simctl (view-only), chosen automatically with the reason shown; third-party
  connectors through the `sim_mirror.connectors` entry point.
- `sim-mirror doctor` with a real test tap, and `serve`, `open`, `config`, `devices`, `token`, `tools` and `version`.
- A local daemon with a Host allowlist, exact Origin checks, hashed scoped tokens, one-shot login codes and embed
  tickets.
- Embedding: `sim_mirror.api` with seams and router factories for Python host applications.
- Build and test tools (`sim_build_run`, `sim_test`) as an opt-in preview.
- A Claude Code plugin and marketplace, an MCP registry entry, examples, a token benchmark and a Homebrew formula
  template.
- Documentation, with the tool, configuration, command-line, protocol and compatibility references generated from the
  code.
