# Changelog

All notable changes to SimMirror are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
