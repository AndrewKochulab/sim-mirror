# Changelog

All notable changes to SimMirror are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **A native connector: SimMirror drives a simulator with its own helper, and idb_companion is no longer needed**
  ([#11](https://github.com/AndrewKochulab/sim-mirror/issues/11)). `sim-mirror-helper`, a Swift program in `helper/`,
  reads a device's framebuffer, streams H.264 from VideoToolbox's low-latency encoder or JPEG, sends touches, buttons
  and keys through the simulator's input service (dtuhid, or Indigo before CoreSimulator 1155.4) and reads the element
  tree, over a unix socket. `auto` now tries `native`, then `idb`, then `simctl`, and a device the helper cannot reach
  -- it does not start in time, or its input does not answer -- falls back to the next connector that can do as much,
  saying why. Measured with the new `benchmarks/connector_latency.py` on iOS 26.5 and 27.0, its first H.264 frame
  comes 250 to 300 ms sooner from attaching than idb's, and its screenshots, snapshots and input are as fast or faster;
  attaching takes 60 to 70 ms longer, spent warming the encoders ([Connectors](docs/connectors.md#native)). idb stays,
  used by `auto` when the helper cannot be, and always with `connectors.preferred = "idb"`.
- **The wheel carries the helper.** A release builds it for both Mac architectures, signs it ad hoc and puts it in the
  wheel, now tagged `macosx_14_0_universal2`; every wheel and the sdist carry its Swift package, and
  `sim-mirror helper build` builds it with the Xcode in use where there is no helper -- a Homebrew install, a git
  checkout. `sim-mirror helper status` says which helper is used and why. The quickstart no longer installs
  idb_companion.
- **Settings for it**, in the settings panel's Connectors tab: `connectors.native.helper_path` (sensitive, like the
  companion's), `hid_transport`, `startup_timeout` and `idle_key_frames`. `connectors.preferred` suggests the
  connectors installed. `sim-mirror doctor` checks the helper, and that it reaches a booted simulator's screen, input
  and element tree without tapping; a missing idb_companion is no longer a warning while the helper works.
- **A screen accessibility says nothing about is read from its pixels**
  ([#14](https://github.com/AndrewKochulab/sim-mirror/issues/14)). A game, a canvas, an app still loading or one whose
  accessibility stopped answering reads as the text macOS's Vision finds in it: text elements with refs to tap and
  text to wait for, and a note that says so. A device whose connector reads no tree -- simctl -- is now offered
  `sim_snapshot` and read the same way. `perception.ocr` (`fallback` by default, `merge`, `off`) says when, and
  `perception.ocr_level`, `ocr_languages`, `ocr_correction`, `ocr_min_confidence` and `ocr_timeout_ms` how; they are
  the settings panel's new **Screen reading** tab. The reader is a small Swift helper SimMirror compiles with the
  scope's Xcode on first use (about 7 seconds) and keeps under its state folder; `sim-mirror doctor` checks it compiles
  and reads. SimMirror now depends on Pillow.
- **The viewer outlines what was read** while `perception.ocr_overlay` is on, over the screen and never in it: a
  `screen_text` message (`protocol/v1/screen-text.schema.json`, `SCREEN_TEXT_MAX_BOXES`) carries each reading's boxes,
  and an empty one clears them before the screen changes. Pointing at a box says its text and how sure the reading
  was. `--sim-mirror-screen-text` themes the boxes; a viewer from before ignores the message.
- **A settle wait lets go of animations that never stop.** `perception.settle` is `perceptual` by default: a coarse
  grid of the screen's brightness is compared with the look the quiet time began with, and small places that keep
  changing -- a spinner, a pulsing dot -- stop being watched, which the answer says. A one-off change still starts the
  quiet time again, a slow fade is not taken for stillness, and a focused field's row is left out on every connector.
  `perception.settle_tolerance` and `perception.settle_grid` tune it; `exact` is 1.0's settling.

- **A Homebrew tap** ([#19](https://github.com/AndrewKochulab/sim-mirror/issues/19)):
  `brew install andrewkochulab/tap/sim-mirror`. The formula lives in
  [AndrewKochulab/homebrew-tap](https://github.com/AndrewKochulab/homebrew-tap), built from the PyPI release, and a
  daily job there updates, builds and tests it once a new release has been on PyPI for a day. The template in
  `packaging/homebrew/` is gone; the tap is the formula's one home.

- **An optional in-app debug SDK for SwiftUI and UIKit hierarchies**
  ([#13](https://github.com/AndrewKochulab/sim-mirror/issues/13)). **SimMirrorKit**, a Swift package at the top of the
  repository, is what an app under development links and starts with `SimMirror.start()`: in a Debug build on the
  simulator it answers SimMirror, on 127.0.0.1 with a new secret each launch, with its own views -- described by class,
  traits and tap gestures, labeled from what they say, the text inside them or their image, cut to what shows and what a
  modal leaves reachable. `.simMirror(...)` names a SwiftUI view, `SimMirror.register(describer:)` says what a custom
  UIKit view is, and reading SwiftUI's debug data is opt-in on iOS 26. Nothing of it is compiled into a Release build
  (`make sdk-release-check`). See [The app SDK](docs/app-sdk.md).
- **Snapshots merge what the app in front shares**, whichever connector drives the device: what accessibility leaves
  out is added -- a tappable card, a hand-drawn control, a tab bar -- and what it found without a label, or what the
  app's developer named, is named in place, so a tap lands where it did. `connectors.app.merge` (on; nothing changes
  while no app shares), `name_unlabeled`, `timeout_ms` and `max_nodes`, in the settings panel's Connectors tab. The
  viewer shows a `<app> · SDK` chip while an app shares, `sim-mirror doctor` has an **app hierarchy** check, and
  `sim-mirror app hierarchy` prints what the app in front shares. The app and SimMirror speak `protocol/app-sdk/v1`, a
  preview with its own version; `examples/app-sdk` is a sample app, and its recordings are the protocol's examples.
- **The app SDK is tested on a simulator**: `make sdk-lint`, `sdk-test`, `sdk-app-test`, `sdk-coverage` (every source
  file at 98%, merged over the package's tests and the sample app's hosted ones with llvm-cov) and
  `sdk-release-check`, and CI's App SDK job runs its tests on its own Xcode.

### Changed

- The idb connector's process handling -- pid files, process groups, ending orphans -- is shared with the native
  helper's (`connectors.helper_process`); what it does is unchanged.
- The settings schema's text rule has `suggestions`, a list a panel may offer for a text setting, or null.
- `mcpbridge`'s client speaks through `platform.json_lines`, which SimMirror's text reader uses too; a program started
  again before its readers first ran is now read rightly.
- `Runtime.build` takes `vision`, the text reader; its `hierarchy` default is now a `CombinedExtraReaders`, the one slot
  more than one kind of extra reader shares.
- Snapshot readers are read at once rather than one after another, and a later reader can name what the first found
  without a label (`NamingReader`). An element with no label that a later reader finds where an earlier one already
  says its value is not added again: Xcode's page indicator on the home screen, which repeated idb's search slider, is
  no longer listed twice.
- `scripts/check_swift_coverage.py` takes `--under`, the sources it gates, so the native helper and the app SDK share it.

## [1.0.0] - 2026-09-17

SimMirror 1.0. The agent tools, protocol v1 -- now with what the daemon's HTTP routes answer -- `sim_mirror.api`, the
viewer package, settings and the command line are stable, and `compat/surface-v1.json` holds every 1.x release to them
(see [Stability](docs/stability.md)). The settings panel's routes and the host routes leave preview, the agent's cursor
stays while the agent works, and SimMirror installs from PyPI, npm and the MCP Registry.

### Added

- **The agent's cursor stays while the agent works.** It used to leave five seconds after each gesture, so it blinked
  out while the agent thought and vanished for a whole build or test run. Now it rests, dimmed, where the agent last
  acted, and leaves `agent.cursor_linger_s` after the agent's last tool call (60 seconds; `0` for the old behaviour). A
  tool call sends a new `working` agent event when it starts, every `WORKING_EVERY_S` (10) while it runs and when it
  ends, saying whether it still runs -- a running call holds the cursor however short its linger -- and every agent
  event carries `linger_ms`; a viewer from before ignores both. The cursor is drawn by the viewer over the
  screen, so no screenshot or recording of the device shows it.

- **What 1.0 promises is written down and checked** ([#5](https://github.com/AndrewKochulab/sim-mirror/issues/5)).
  [`compat/surface-v1.json`](compat/surface-v1.json) records the surface -- `sim_mirror.api`'s names with their
  parameters and fields, protocol `v1`'s schemas, constants and close codes, the tools' arguments and `sim_act`'s steps,
  the settings and their variables, the commands and flags, and the viewer package's exports, element, events, parts,
  custom properties and option types -- and `scripts/surface.py`, run by the tests, names every way the code stops
  keeping it, while an addition passes. [Stability](docs/stability.md) says what is and is not covered.
- **What the daemon's routes answer is in the protocol**: `protocol/v1/http.schema.json` describes the health check,
  stopping and choosing a device, the device list, embed tickets, spending a code, an agent's lease, a host's token
  records, the agent manifest and a tool's result, and a refusal. A contract test drives a daemon through every route a
  host, a viewer and an agent use and validates each answer. `sim_mirror.protocol` and the viewer package export the
  new types.

- The daemon's log names the encoding each viewer is streamed, what the viewer decodes and what was offered -- `a viewer
  of <udid> streams h264: it decodes h264, jpeg, and h264, jpeg is offered` -- which is what a viewer stuck on JPEG comes
  down to ([Troubleshooting](docs/troubleshooting.md#only-jpeg-never-h264)).
- Safari is verified in the [compatibility table](docs/compatibility.md)
  ([#8](https://github.com/AndrewKochulab/sim-mirror/issues/8)): Safari 26.6.2 decoded H.264, and took a person's tap and
  an agent's, with its cursor. Firefox, Edge, Xcode 16.4, macOS 15, Codex and Cursor stay expected until a real run or
  a compatibility report checks them.

### Changed

- **The settings routes and seams, the host routes, `DaemonHost` and the names beside them are no longer preview**:
  they are stable with the rest from 1.0.
- `/healthz` always sends `proof` and `token_proof`, `null` when not asked for, as the protocol's rule that every
  property is always sent requires. A command line or host that treated a missing proof as none reads `null` the same.
- `DaemonHost`'s methods are typed with the protocol's shapes (`ScopeStatus`, `Started`, `DeviceChoice`,
  `SettingsView`, `TokenRecord`, `MadeToken`) instead of `dict[str, Any]`; they answer the same dicts.
- Only the host's parameters of `Runtime.build` are promised: `config`, `state`, `policy`, `memory`, `copy`, `usage` and
  `may_share`. The rest, and `Runtime`'s fields, are how SimMirror's own tests assemble one. The connector entry point
  is not stable in 1.x either; the [connector guide](docs/contributing/connector-guide.md) says to pin a minor version.
- **SimMirror installs from PyPI, and its viewer from npm** ([#6](https://github.com/AndrewKochulab/sim-mirror/issues/6)):
  `uv tool install sim-mirror==0.2.0`, `uvx --from sim-mirror==0.2.0 sim-mirror mcp` in client configurations and the
  plugin, and `npm install @andrewkochulab/sim-mirror@0.2.0` -- where every install named a release tag or a release
  asset. `check_distribution.py` holds those pins to the package's version as it did the tags.
- The release workflow publishes to npm by trusted publishing, as it already did to PyPI, so no npm token is stored.
- **SimMirror is in the [MCP Registry](https://registry.modelcontextprotocol.io)** as
  `io.github.AndrewKochulab/sim-mirror` ([#7](https://github.com/AndrewKochulab/sim-mirror/issues/7)): 0.2.0 was listed
  by hand, and the release workflow lists each later version once PyPI has it, signing in to the registry with GitHub
  Actions' OIDC token and a publisher pinned by version and checksum.

## [0.2.0] - 2026-09-17

Build and test leave preview; text reaches iOS 27 again; a settings panel in the viewer; Xcode 27's UI hierarchy read
through `mcpbridge`; and several applications sharing one daemon. The settings routes, the host routes and the names
they added to `sim_mirror.api` are preview until 1.0 -- see [Stability](docs/stability.md).

### Added

- **Several applications can share one daemon** ([#15](https://github.com/AndrewKochulab/sim-mirror/issues/15)), each
  with a **host token** for its namespaces instead of an admin token or a SimMirror of its own. See
  [Sharing the daemon between hosts](docs/embedding/shared-daemon.md):
  - `sim-mirror token create --kind host --scope 'notes:*' --root DIR` gives an application every scope whose id
    starts `notes:`, and no two hosts one namespace. A host reaches those scopes' person routes, embed tickets and
    settings (one scope at a time, a sensitive one confirmed at the terminal), and makes, lists and revokes `agent`
    and `viewer` tokens for them under `/api/v1/host/tokens`, naming only folders inside its own. Revoking a host
    revokes every token it made.
  - **Devices stay apart**: a scope never joins or picks a simulator another host's scope -- or the Mac's -- is
    running, and a picker leaves those out. A host's scopes are a group of their own, named for the namespace.
  - `sim_mirror.api.DaemonHost` does all of it from Python, answering `AgentAccess` -- an MCP server's command line,
    with the agent token in its environment only -- and raising `DaemonRefused` or `DaemonUnavailable`. It sends its
    token only after `/healthz` proves, with `token_proof`, that the listener knows it.
  - Scoped tokens take namespaces (`notes:*`) beside scope ids and `*`, and say which host made them.
  - `Runtime.build` takes `may_share`, whether two scopes may use one device; `SettingsEditor` has
    `may_write_every_scope`.

  Checked on 7491 with two hosts, over HTTP: each was refused the other's scopes, device and namespace, the Chrome
  embed page of one showed and took taps, Claude Code drove it through an agent token the host made, and revoking that
  host cut the agent off while the other kept working.

- **Xcode 27's UI hierarchy, through `mcpbridge`** ([#12](https://github.com/AndrewKochulab/sim-mirror/issues/12)).
  Measured on Xcode 27.0 with iOS 27.0 before it was built -- see [Connectors](docs/connectors.md#mcpbridge):
  - **A new `mcpbridge` connector**, chosen by name: the screen as simctl shows it, and snapshots read through Xcode's
    UI hierarchy, on a Mac without idb_companion. It offers no input: a tap through Xcode's tools answered after 3.3
    seconds.
  - **`connectors.mcpbridge.merge`**, off by default: a device idb drives is read both ways, and a snapshot adds what
    Xcode's hierarchy has and idb's accessibility tree does not -- Safari's heading, text and links on example.com
    (5 elements to 8, and an agent tapped the link), a widget's text, the status bar. Each snapshot takes 0.2 to 0.9
    seconds longer.
  - **`sim-mirror xcode approve`** has Xcode approve SimMirror to use its tools, which Xcode does for an agent that
    opens a project through them: it opens this folder's project, or the one named, and closes it again.
  - A simulator can be in one Xcode session at a time, so SimMirror's is ended a minute after its last read; one it
    left behind is taken back, and another agent's is named and left alone. Xcode never opens: mcpbridge reaches
    Xcode's tool service, which runs without a window, and follows `device.developer_dir` while `xcode-select` names
    Xcode 26.6.
  - `sim-mirror doctor` has an `xcode tools` check, which reads a booted simulator through Xcode when a scope does.
  - Readers merge by what an element says and where it is, whatever each reader calls it, and a snapshot says why a
    merged reader could not read. `Runtime.build` takes `hierarchy`, what snapshots merge in besides a connector's own
    tree; `ConnectorContext` carries `xcrun`; `HostCopy` has `xcode_approve_command`.
  - `sim_mirror.testing` has `FakeBridge`, Xcode's tools as Xcode 27.0 answered, and fixtures of the hierarchies and
    accessibility trees of six screens read both ways.

  **Not done, and why:** Xcode 26.6's `mcpbridge` is not used. It reaches only an Xcode that is open, and SimMirror does
  not open Xcode, so its tools were not measured.

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
- The viewer's spacing scale has `--sim-mirror-space-5` to `--sim-mirror-space-7` (12px to 24px), which the settings
  panel uses to give each setting room.
- The configuration reference says, for every setting, when a change takes effect, whether a scope may have its own
  value, and whether it is sensitive.

- **Build and test leave preview.** `sim_build_run` and `sim_test` are covered by
  [the stability policy](docs/stability.md) like the other tools, and stay off by default in a standalone install,
  since they run commands. Measured with a probe project on Xcode 26.6 and 27.0:
  - **`sim_test` takes `destination`** -- `{"name": "iPhone 17"}` or `{"udid": …}`, with `"runtime"` to choose between
    simulators of one name -- to run the tests on another simulator on this Mac without changing the one the project
    shows. Not one another project is running or testing on, nor one another process has claimed; a name that
    matches none or several is refused with the ones there are. [Security](docs/security.md) says so.
  - **A failing test is named as `only_testing` takes it back** -- `ProbeTests/TripTests/testDeliberatelyFails`,
    `ProbeTests/ParsingTests/countsTrips()` -- read from the test's URL, the one place a result bundle names the
    target, so an agent can run exactly that test again.
  - **A failure's file is shown where the project has it**, `Tests/Suites/ParsingTests.swift:6` rather than the bare
    `ParsingTests.swift:6` Xcode 26.6 gives, when only one file has that name, and relative to the project folder
    where Xcode 27.0 gives the whole path.
  - **Tests that do not build are answered with the compile errors** (`test FAILED · … · the tests did not build`).
    Such a run's summary says "unknown" and no tests, and its errors are only in the build's results, so it used to
    answer "0 passed, 0 failed".
  - Both tools take **`warnings`** to list warnings beside errors, and describe `test_plan` and `retries`.
  - **`build.test_diagnostics`**, off by default: whether a failed test run also collects the simulator's diagnostics.
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
  Xcode 26.6, chosen both ways: the screen, touches, keys, typing, the element tree and the agent tools.

### Fixed

- **Typed text reaches an iOS 27 device.** Text went in as a paste, which iOS 27 refuses without a prompt
  ([#27](https://github.com/AndrewKochulab/sim-mirror/issues/27)). It is now typed as key presses when every character
  is on a US keyboard and the Mac's layout is US or ABC -- measured to arrive on iOS 26.5 and 27.0, without iOS 26's
  "Allow Paste" prompt either -- and pasted only otherwise. **`device.typing`** (`auto`, `keys`, `paste`) chooses; an
  agent's step that pastes to iOS 27 says why it pasted and that the paste may have been refused. Typed text gets iOS's
  smart punctuation, as a person's does.
- **A scheme, configuration or test plan named like `App (Staging)` can be built.** A refusal listed it among the
  names there were, then refused it when an agent used it: names were held to a pattern narrower than Xcode's. Any
  one-line name that does not start with `-` is now taken, and every name a refusal offers is one a call may give.
- **A test failure on Xcode 27 says where it happened.** Xcode 27.0 took the `File.swift:6:` out of the failure's
  message and put a `sourceLocation` beside it, so every failure was listed with no place. Both are read now.
- **A failed test run no longer goes on for up to ten minutes before answering.** xcodebuild collects the simulator's
  diagnostics after a failure by default -- `simctl diagnose --timeout=600`, measured on Xcode 26.6 and 27.0 --
  which the answer never needed. `sim_test` passes `-collect-test-diagnostics never` unless
  `build.test_diagnostics` is on.
- **Build and test refusals say what to do next**: a project that is not there lists the ones that are; a
  configuration the project lacks lists its configurations; no schemes says how to share one; a missing xcrun points
  at `sim-mirror doctor`; a run stopped at its limit names `build.timeout_minutes`; an unknown `build_id` lists recent
  runs; a build with no app to launch names the other schemes; an agent with no folder to build in is told how to
  give it one.
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

- `sim_mirror.testing.guards` refuses `mcpbridge` too; `FakeBridge` stands in for it.
- The doctor's tap check, on a connector chosen by name that cannot touch the device, says to choose one that can
  instead of saying to install idb_companion.
- `sim_mirror.testing.guards` refuses `defaults` too, which SimMirror now runs to read the Mac's keyboard layout;
  `FakeKeyboard` stands in for it, and `Runtime.build` takes `keyboard_is_us` for a host that wants to answer itself.
- **Test ids in `sim_test`'s answers carry their target, and an XCTest method has no `()`**:
  `ProbeTests/TripTests/testDeliberatelyFails` where it said `TripTests/testDeliberatelyFails()`. The old form could
  not be passed back to `only_testing`. `only_testing` and `skip_testing` accept ids with a colon or spaces.
- `build.configuration` accepts any build configuration name Xcode does, such as `Beta (Internal)`; one that starts
  with `-` is refused, as before.
- `HostCopy` has `build_folder_hint` and `no_build_folder()`, for a host to say how an agent gets a folder to build in.
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
