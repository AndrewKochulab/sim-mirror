# SPDX-License-Identifier: Apache-2.0
"""``python -m sim_mirror``: the same as the ``sim-mirror`` command."""

from sim_mirror.cli.main import main

if __name__ == "__main__":  # pragma: no cover - run as a module
    raise SystemExit(main())
