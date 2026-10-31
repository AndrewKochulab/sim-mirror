# Roadmap

SimMirror's plans, grouped by milestone. Each item is tracked as an issue in its
[milestone](https://github.com/AndrewKochulab/sim-mirror/milestones). Dates are not promised; order is.

Ideas and votes are welcome in [Discussions](https://github.com/AndrewKochulab/sim-mirror/discussions).

Released: v0.1 (0.1.0), v0.2 (0.2.0), v1.0 (1.0.0), v1.2 (1.2.0), v2.0 (2.0.0), v2.1 (2.1.0), v2.2 (2.2.0),
v2.3 (2.3.0), v2.4 (2.4.0), v2.5 (2.5.0) and v2.6 (2.6.0); see the [changelog](CHANGELOG.md).

## v0.1: first public preview

- Live viewer (H.264 over WebCodecs, JPEG fallback) in a browser tab, an iframe or the `<sim-mirror>` web component.
- Person control: touch, drag, scroll, typing, paste, hardware buttons, light and dark appearance, device picker.
- Agent control over MCP: `sim_device`, `sim_snapshot`, `sim_screenshot`, `sim_act`, `sim_app`.
- Agent cursor: every agent gesture announced and drawn before it lands.
- Screen understanding: accessibility snapshots with stable refs, digests and diffs; waits for elements and settling.
- Connectors: idb (full control) and simctl (view-only fallback), selected automatically with the reason shown.
- `sim-mirror doctor` with a real test tap.
- Local daemon with scoped tokens, one-shot login codes and embed tickets.
- Build and test tools (`sim_build_run`, `sim_test`) as an opt-in **preview**.
- Claude Code plugin, examples for Codex, Cursor, iframes, web components and FastAPI embedding.

## v0.2: build, test and shared sessions

- `sim_build_run` and `sim_test` leave preview: schemes, destinations and test plans.
- Test results summarised from `.xcresult` with failures linked to source lines.
- A settings panel in the viewer.
- An Xcode 27 `mcpbridge` connector and reader for the UI hierarchy.
- One shared daemon serving several hosts at once.

## v1.0: stable

- Protocol `v1` and the Python embedding API (`sim_mirror.api`) declared stable, with semantic versioning.
- Packages on PyPI and npm with provenance.
- Listing in the official MCP registry.
- Compatibility matrix verified across supported Xcode versions.

## v1.2: deeper screen understanding

- A native Swift helper connector: frames, input and the element tree without idb_companion, used first by `auto`.
- Optional in-app debug SDK for SwiftUI and UIKit hierarchies without accessibility labels.
- OCR and vision fallback reader; perceptual settle detection.
- A Homebrew tap: `brew install andrewkochulab/tap/sim-mirror`.

## v2.0: real devices

Released in 2.0.0 ([Real devices](docs/real-devices.md)):

- Tier 1, no signing: device list, install, launch, logs, appearance, accessibility and a simulated location through
  `devicectl`, and the live screen over the cable.
- Tier 2, WebDriverAgent: full touch, typing and element trees for any app, built with your own team from a pinned,
  checked release and reached only over the cable.
- Every change to a device put back when it is let go, and after a crash.
- Recording a simulator's or a device's screen as an MP4 or a GIF, with touches drawn
  ([Recording](docs/recording.md)).

## v2.1: walkthroughs that hold up

Released in 2.1.0:

- Apps turned to landscape read, touched and looked at in their own points, on every connector, and `sim_device rotate`.
- What a tab bar, a toolbar, a navigation bar or the keyboard covers said in a snapshot, and a tap on it refused.
- `sim_build_run` and `sim_test` build the git worktree the agent works in, with its scheme's configuration, and say
  which source they built.
- Observe mode, refusing what reads like Delete, Save, Send or Sign out unless a call confirms it.
- `sim_device stop` and `sim-mirror stop`, and a session that ends when its simulator is shut down elsewhere.
- Recordings small enough for a pull request, from `sim_record`, `sim-mirror record` and the viewer.

## v2.2: Android

Released in 2.2.0:

- Android emulators and phones with the same viewer, cursor and tools ([Android](docs/android.md)): adb for input and
  the element tree, scrcpy's server for the live screen, and `sim_build_run` and `sim_test` with a project's Gradle
  wrapper.
- `sim-mirror android` and the doctor's Android checks.

## v2.3: real devices that recover

Released in 2.3.0:

- A real device attached again by itself -- the cable screen and WebDriverAgent with it -- once a pulled cable is
  back, keeping the session it had until the new one is up ([real devices](docs/real-devices.md)).
- `sim_device health`, checking the screen, reading and touch on the device now; touch said ready only while
  WebDriverAgent answers, in the viewer, `sim-mirror wda status` and the doctor's `touch now` check.
- An agent told why a real device cannot be touched: `sim_act` kept and refused with the device's note, which
  `sim_device info` and pixel-read snapshots carry too.
- A text reader a slow reading no longer locks out, warmed as it starts.
- The plugin's server named `device`: `plugin:sim-mirror:device`.

## v2.4: agents that hold up on a real iPhone

Released in 2.4.0, from a two-day field report of agents testing an app on a real iPhone, and every case run again on
an iPhone 17 Pro on iOS 27.0:

- WebDriverAgent a person stopped left stopped until an agent asks for the phone again, an idle agent no longer holding
  a device, and `sim-mirror stop --device` and the holders it names.
- Recordings as long as they ran, never readable half-written nor lost, with `max_seconds` and a `label`.
- Long screens read without locking WebDriverAgent up, a tap never sent late behind them, and a cold first read given
  time.
- `sim_act` waits for exact words, a regex or a state that holds, up to two minutes, with times for each step; refs
  read again before a tap when the screen may have moved; a swipe waited out before what follows reads the screen.
- iOS 27's floating search bar, said to cover the rows it takes taps from.
- One agent at a time on a real device (`device.exclusive`), with `steal`.
- An agent told what is wrong: WebDriverAgent's last error, the helper's own, `sim_device health`'s text reader and
  live reading, and `wda status` and the doctor for every device.
- `sim_screenshot save_to`, app logs by the app's executable, and a time on every line of `daemon.log`.

## v2.5: agent workflows

Released in 2.5.0:

- Four concise agent skills -- app interaction, setup and recovery, builds and tests, evidence capture -- with a
  portable archive for Codex and Cursor.
- Tool results that say which steps completed and failed, and builds and tests that say how far they got.

## v2.6: recordings as smooth as the screen

Released in 2.6.0, from a field report of recording a short animation on a real iPhone
([#133](https://github.com/AndrewKochulab/sim-mirror/issues/133)), and every item run again on an iPhone 17 Pro on iOS
27.0:

- Recordings at 30, 60 or 120 frames a second, a real or an Android device's stream boosted to the recording's rate
  and its screen's own width while it runs, and fast-start MP4s a browser plays at once.
- How fast the screen sent its pictures said when a recording stops, with a warning when it stuttered or was shown by
  screenshot; a cabled iPhone's live screen tried again by itself.
- A recording of a region of the screen; Do Not Disturb on Android while recording.
- Refs kept through a selection change, switches and unlabelled controls guarded in observe mode, and the 20-step limit
  said up front.
- An app put back in front after an appearance change, WebDriverAgent's start-up waited out, `save_to` in a worktree,
  and `sim-mirror config scope`.

## Next: macOS apps and the Mac

Planned in [#138](https://github.com/AndrewKochulab/sim-mirror/issues/138):

- A `mac` device kind, macOS builds and tests, window capture and pointer and keyboard input
  ([#135](https://github.com/AndrewKochulab/sim-mirror/issues/135)).
- An app-scoped mode that drives one app from inside it, never the person's pointer or keyboard
  ([#136](https://github.com/AndrewKochulab/sim-mirror/issues/136)).
- An opt-in mode, off by default, that drives the whole Mac
  ([#137](https://github.com/AndrewKochulab/sim-mirror/issues/137)).

## Website and launch

- Landing page, documentation site, video tutorials and walkthroughs.
