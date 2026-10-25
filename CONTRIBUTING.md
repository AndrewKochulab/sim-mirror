# Contributing to SimMirror

Thanks for helping. Bug reports, compatibility reports, docs fixes, connectors and features are all welcome.

## Before you start

- Questions and ideas go to [Discussions](https://github.com/AndrewKochulab/sim-mirror/discussions).
- Bugs and compatibility reports go to [issues](https://github.com/AndrewKochulab/sim-mirror/issues/new/choose).
  Please attach the output of `sim-mirror doctor --json`.
- For anything larger than a small fix, open an issue first so we can agree on the approach.
- Security issues are reported privately: see [SECURITY.md](SECURITY.md).

## Setup

You need macOS on Apple Silicon, Xcode, [uv](https://docs.astral.sh/uv/) and Node.js 20 or newer.

```sh
git clone https://github.com/AndrewKochulab/sim-mirror.git
cd sim-mirror
make install      # uv sync + npm ci in viewer/
make lint         # every static check
make test         # Python and viewer tests
make coverage     # tests with the coverage gates
```

The native helper is a Swift package in `helper/`:

```sh
make helper-coverage                          # its core's tests, with the coverage gate
make helper-build                             # the release build, for both Mac architectures
SIM_MIRROR_LIVE_UDID=<booted UDID> make live  # the native connector on a real simulator; taps its screen
```

A real device's lifecycle has a run of its own, on a phone plugged in by cable and unlocked. It reconnects the phone,
asks you to unplug it and plug it back in, puts back what it changed after a crash, and fails if anything would ask
the phone to reboot, shut down or be erased -- refusing that call before it is sent. Neither CI nor `make test` runs
it, and without the UDID every test is skipped:

```sh
SIM_MIRROR_LIVE_DEVICE_UDID=<the phone's UDID> make live-device                     # view only
SIM_MIRROR_LIVE_DEVICE_UDID=<udid> SIM_MIRROR_LIVE_DEVICE_WDA=1 \
  SIM_MIRROR_LIVE_DEVICE_TEAM=<team> make live-device                               # driven by WebDriverAgent
```

An Android emulator has a run of its own too, on one already running -- from Android Studio, or picked in the
viewer. It runs the connector contract through adb, then through scrcpy when a server is named, and builds and tests
`examples/android-app` with Gradle when asked. It never starts or stops the emulator:

```sh
SIM_MIRROR_LIVE_ANDROID_UDID=<avd-… id> make live-android                          # through adb
SIM_MIRROR_LIVE_ANDROID_UDID=<id> SIM_MIRROR_LIVE_SCRCPY=<scrcpy-server 5.0.1> \
  SIM_MIRROR_LIVE_GRADLE=1 make live-android                                       # scrcpy, and Gradle
```

What a phone's run cannot do unattended:

- **Unplugging needs a person**, and a phone Xcode also reaches over the network stays connected with its cable out.
  When asked to unplug, also turn on Airplane mode and check that Wi-Fi is off in Control Center (Airplane mode keeps
  Wi-Fi on if you turned it on there before); turn it back off before plugging in again.
- **The WebDriverAgent run needs it set up first** (`sim-mirror wda setup --device <udid>`, with the team in
  `real_devices.team_id`) and a team with room for the phone among its registered devices; it fails unless the
  phone is then offered `sim_act`. Nothing is tapped in either run.
- **A run nobody types into** -- an agent's -- asks through a folder instead of the terminal:
  `SIM_MIRROR_LIVE_DEVICE_ASK_DIR=<dir>` writes each step to `<dir>/asked` and waits up to half an hour for
  `<dir>/answered`.

The app SDK is the Swift package at the top of the repository, with a sample app in `examples/app-sdk/`. Its tests
run on an iOS simulator, with the Xcode `SDK_DEVELOPER_DIR` names and the simulator `SDK_DESTINATION` does:

```sh
make sdk-lint sdk-test sdk-app-test   # swift-format, the package's tests and the sample app's hosted ones
make sdk-coverage                     # both, with every SDK source file at 98%; on an iOS 26 simulator
make sdk-release-check                # a Release build of the sample app holds none of the SDK
```

## Rules the checks enforce

- **Tests never touch a real Simulator.** No test may start `xcrun`, `xcodebuild`, `idb_companion`, `sim-mirror-helper` or a
  browser. Use
  the fakes in `sim_mirror.testing`. A guard in `tests/conftest.py` fails any test that tries.
  `@pytest.mark.allow_subprocess` is only for tests of the process layer that run a stand-in binary.
- **Coverage is per file.** Every Python and TypeScript file keeps at least 98% line and branch coverage, and so does
  every file of the native helper's `HelperCore` (`make helper-coverage`) and of the app SDK (`make sdk-coverage`).
- **External programs have one owner each.** `xcrun`/`simctl` and `git` run only from `sim_mirror/platform/`,
  `idb_companion` only from the idb connector, `sim-mirror-helper` only from the native connector, and `xcodebuild` only from `sim_mirror/build/` (`scripts/check_containment.py`).
- **Generated files are committed and checked.** After changing a protocol schema, a tool, a setting, a command or
  `compat/matrix.toml`, run `make generate`; after changing the viewer, run `make viewer-bundle`, which rebuilds its
  committed page bundle and checks the 45 KB size budget. CI fails when a generated file or the bundle is stale.
- **Docs link to what is there.** `scripts/check_links.py` checks every relative link and anchor, and
  `scripts/check_media_sizes.py` keeps pictures in `docs/media` small.
- **Every source file starts with** `SPDX-License-Identifier: Apache-2.0`.
- Python: Ruff (line length 120) and `mypy --strict`. TypeScript: ESLint, Prettier and `tsc --noEmit`.
- Keep modules small and single-purpose; add a seam rather than a special case.

## Commits and pull requests

- One logical change per commit, with a subject that says what changes in behaviour.
- Update `CHANGELOG.md` under **Unreleased** for anything a user would notice.
- Fill in the pull request template, including how you verified the change.
- Changes that affect the Simulator screen should include a screenshot or a short recording.

## Adding a connector

Connectors are how SimMirror reaches a device. Start from [examples/custom-connector](examples/custom-connector/) and
read the [connector guide](docs/contributing/connector-guide.md): a connector declares its capabilities, probes its
environment, and passes the shared contract tests in `tests/contract/`.

## License

By contributing, you agree that your contributions are licensed under the [Apache License 2.0](LICENSE).
