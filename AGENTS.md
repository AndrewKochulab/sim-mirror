# Guide for AI coding agents

This file is for AI agents (Claude Code, Codex, Cursor and others) working **on** SimMirror's source. Human
contributors should read [CONTRIBUTING.md](CONTRIBUTING.md); its rules apply to agents too.

## What this repository is

SimMirror mirrors and drives the iOS Simulator: a Python daemon and library (`src/sim_mirror/`), a TypeScript viewer
published to npm (`viewer/`), a stdlib-only MCP relay, a Claude Code plugin (`plugins/sim-mirror/`), examples and docs.

## Commands

```sh
make install     # uv sync + npm ci in viewer/
make lint        # ruff, mypy --strict, containment and host-neutral checks, generated-file checks, viewer lint
make test        # pytest + vitest
make coverage    # the same with per-file coverage gates (98%)
make generate    # regenerate protocol types, the reference pages and the compatibility table
```

After changing the viewer, run `make viewer-bundle`: it rebuilds the committed page bundle and checks its size budget.

Use `uv run …` for Python. Never create a virtualenv by hand or use pip.

## Rules

1. **Never start a real Simulator, `xcrun`, `xcodebuild`, `idb_companion` or a browser from a test.** Use
   `sim_mirror.testing` fakes. The conftest guard fails the test otherwise.
2. **Keep each external program in its one module**: `platform/` for xcrun and simctl, `connectors/idb/companion.py`
   for idb_companion, `build/xcodebuild.py` for xcodebuild.
3. **Hosts embed through `sim_mirror.api` only.** Don't make hosts import internals; add to `api.py` deliberately.
4. **Stay host-neutral.** SimMirror knows scopes, not any host application's concepts. `scripts/check_host_neutral.py`
   enforces the vocabulary.
5. **The protocol has one source**, `protocol/v1/*.schema.json` and `close-codes.json`. Edit those and run
   `make generate`; never hand-edit `_generated.py` or `protocol.generated.ts`.
6. **Secrets never go in argv, URLs or logs.** Tokens travel in headers or environment variables; tickets and login
   codes are one-shot and redacted.
7. **Per-file coverage stays at or above 98%**, and a bug fix comes with a test that fails without it.
8. Every source file begins with an `SPDX-License-Identifier: Apache-2.0` comment.
9. **1.x keeps what 1.0 promised.** `compat/surface-v1.json` is the promise and `scripts/surface.py` (run by the tests)
   names every break of it. Add freely -- an optional parameter, a field, an enum member, a flag -- but never regenerate
   that file to make a break pass: a break waits for version 2 (`docs/stability.md`).
