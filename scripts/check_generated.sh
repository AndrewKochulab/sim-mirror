#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# Fail when a generated file is out of date with its source. `make generate` rewrites them all.
set -eu
cd "$(dirname "$0")/.."
uv run python scripts/gen_protocol.py --check
uv run python scripts/gen_docs.py --check
