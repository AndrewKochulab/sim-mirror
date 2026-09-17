# Roadmap

SimMirror's plans, grouped by milestone. Each item is tracked as an issue in its
[milestone](https://github.com/AndrewKochulab/sim-mirror/milestones). Dates are not promised; order is.

Ideas and votes are welcome in [Discussions](https://github.com/AndrewKochulab/sim-mirror/discussions).

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

## v1.0: stable

- Protocol `v1` and the Python embedding API (`sim_mirror.api`) declared stable, with semantic versioning.
- Packages on PyPI and npm with provenance.
- Listing in the official MCP registry.
- A Homebrew tap: `brew install andrewkochulab/tap/sim-mirror`.
- Compatibility matrix verified across supported Xcode versions.

## v1.1: build and test

- `sim_build_run` and `sim_test` leave preview: schemes, destinations and test plans.
- Test results summarised from `.xcresult` with failures linked to source lines.

## v1.2: deeper screen understanding

- A native Swift helper connector: faster frames and input without idb_companion.
- An Xcode 27 `mcpbridge` connector and reader for the UI hierarchy.
- Optional in-app debug SDK for SwiftUI and UIKit hierarchies without accessibility labels.
- OCR and vision fallback reader; perceptual-hash settle detection.
- One shared daemon serving several hosts at once.

## v2.0: real devices

- Tier 1, no signing: device list, install, launch, logs and a USB screen mirror.
- Tier 2, WebDriverAgent: full touch, typing and element trees for any app.

## Android

- Android emulator connector (adb input and a scrcpy-style stream) with the same viewer, cursor and tools.

## Website and launch

- Landing page, documentation site, video tutorials and walkthroughs.
