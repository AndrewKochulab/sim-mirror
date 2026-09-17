# SPDX-License-Identifier: Apache-2.0
"""The license header check: every source file names Apache-2.0 near its top; generated and built files are left out."""

from __future__ import annotations

from pathlib import Path

import pytest

import check_license_headers as check

HEADER = "# SPDX-License-Identifier: Apache-2.0\n"


def test_the_repository_is_clean(capsys: pytest.CaptureFixture[str]) -> None:
    assert check.main() == 0
    assert "license headers ok" in capsys.readouterr().out


def _write(root: Path, rel: str, content: str | bytes) -> str:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content)
    return rel


def test_a_source_file_without_the_header_is_named(tmp_path: Path) -> None:
    files = [
        _write(tmp_path, "src/ok.py", HEADER + "x = 1\n"),
        _write(tmp_path, "scripts/run.sh", "#!/bin/sh\n# SPDX-License-Identifier: Apache-2.0\n"),
        _write(tmp_path, "viewer/src/a.ts", "// SPDX-License-Identifier: Apache-2.0\n"),
        _write(tmp_path, "src/bare.py", "x = 1\n"),
        _write(tmp_path, "src/late.py", "\n" * 6 + HEADER),
        _write(tmp_path, "viewer/src/b.ts", "export {}\n"),
        _write(tmp_path, "src/binary.py", b"\xff\xfe"),
        _write(tmp_path, "helper/Package.swift", "// swift-tools-version:6.0\n// " + HEADER[2:]),
        _write(tmp_path, "helper/Sources/Guard/include/Guard.h", "#import <Foundation/Foundation.h>\n"),
        _write(tmp_path, "helper/Sources/Guard/Guard.m", "// SPDX-License-Identifier: Apache-2.0\n"),
    ]
    assert check.missing(tmp_path, files) == [
        "src/bare.py",
        "src/late.py",
        "viewer/src/b.ts",
        "src/binary.py",
        "helper/Sources/Guard/include/Guard.h",
    ]


def test_other_file_kinds_generated_stubs_and_bundles_are_not_its_business(tmp_path: Path) -> None:
    files = [
        _write(tmp_path, "README.md", "# no header\n"),
        _write(tmp_path, "src/sim_mirror/connectors/idb/proto/idb_pb2.py", "# generated\n"),
        _write(tmp_path, "src/sim_mirror/server/static/viewer/app.js", "minified\n"),
        _write(tmp_path, "viewer/dist/index.js", "built\n"),
    ]
    assert check.missing(tmp_path, files) == []


def test_it_lists_what_is_missing_when_it_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, "src/bare.py", "x = 1\n")
    monkeypatch.setattr(check, "repo_files", lambda root: ["src/bare.py"])
    assert check.main(tmp_path) == 1
    assert "src/bare.py" in capsys.readouterr().err
