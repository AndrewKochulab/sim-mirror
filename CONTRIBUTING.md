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

## Rules the checks enforce

- **Tests never touch a real Simulator.** No test may start `xcrun`, `xcodebuild`, `idb_companion` or a browser. Use
  the fakes in `sim_mirror.testing`. A guard in `tests/conftest.py` fails any test that tries.
  `@pytest.mark.allow_subprocess` is only for tests of the process layer that run a stand-in binary.
- **Coverage is per file.** Every Python and TypeScript file keeps at least 98% line and branch coverage.
- **External programs have one owner each.** `xcrun`/`simctl` run only from `sim_mirror/platform/`, `idb_companion`
  only from the idb connector, and `xcodebuild` only from `sim_mirror/build/` (`scripts/check_containment.py`).
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
