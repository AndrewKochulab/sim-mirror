# SPDX-License-Identifier: Apache-2.0
"""WebDriverAgent's source: only the pinned release, only when its archive is the one pinned, only inside its folder."""

from __future__ import annotations

import hashlib
import io
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from sim_mirror.connectors.iphone import wda_source as module
from sim_mirror.connectors.iphone.wda_source import (
    MARKER,
    MJPEG_ON_LOOPBACK,
    PINNED,
    PROJECT,
    WdaRelease,
    WdaSourceError,
    configured,
    download,
    patched,
    stamp,
    wda_source,
)
from sim_mirror.testing.wda import COMMIT, FOLDER, SERVER, wda_archive

GOOD = wda_archive(
    (FOLDER, None),
    (f"{FOLDER}/{PROJECT}", None),
    (f"{FOLDER}/{PROJECT}/project.pbxproj", b"// project"),
    (f"{FOLDER}/{MJPEG_ON_LOOPBACK.path}", SERVER),
    (f"{FOLDER}/Scripts/embed-runner-icon.sh", b"#!/bin/sh\n"),
    script=True,
)


def release(data: bytes) -> WdaRelease:
    return WdaRelease("1.0.0", COMMIT, hashlib.sha256(data).hexdigest())


class Fetches:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.urls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        return self.data


def test_the_pinned_release_is_fetched_from_github_by_its_commit() -> None:
    assert PINNED.url == f"https://codeload.github.com/appium/WebDriverAgent/tar.gz/{PINNED.commit}"
    assert PINNED.folder == f"WebDriverAgent-{PINNED.commit}" and len(PINNED.sha256) == 64


def test_a_release_is_fetched_checked_and_unpacked_once_then_kept(tmp_path: Path) -> None:
    fetch = Fetches(GOOD)
    folder = wda_source(tmp_path, release(GOOD), fetch=fetch)
    assert folder == tmp_path / FOLDER and (folder / PROJECT / "project.pbxproj").read_bytes() == b"// project"
    server = folder / MJPEG_ON_LOOPBACK.path
    assert (
        server.stat().st_mode & 0o777 == 0o644 and (folder / "Scripts" / "embed-runner-icon.sh").stat().st_mode & 0o111
    )
    assert MJPEG_ON_LOOPBACK.new in server.read_text() and MJPEG_ON_LOOPBACK.old not in server.read_text().replace(
        MJPEG_ON_LOOPBACK.new, ""
    )
    assert (folder / MARKER).read_text() == stamp(release(GOOD)) and stamp(release(GOOD)) != stamp(release(GOOD), ())
    assert wda_source(tmp_path, release(GOOD), fetch=fetch) == folder and len(fetch.urls) == 1
    assert [path.name for path in tmp_path.iterdir()] == [FOLDER], "nothing staged is left behind"
    (folder / MARKER).write_text("another archive")
    assert wda_source(tmp_path, release(GOOD), fetch=fetch) == folder and len(fetch.urls) == 2, "unpacked again"
    (folder / MARKER).unlink()
    assert wda_source(tmp_path, release(GOOD), fetch=fetch) == folder and len(fetch.urls) == 3, "and when unmarked"


def test_an_archive_that_is_not_the_pinned_one_is_never_unpacked(tmp_path: Path) -> None:
    pinned = WdaRelease("1.0.0", COMMIT, "0" * 64)
    with pytest.raises(WdaSourceError, match="is not the one SimMirror pins: its SHA-256 is"):
        wda_source(tmp_path, pinned, fetch=Fetches(GOOD))
    assert not tmp_path.joinpath(FOLDER).exists()


@pytest.mark.parametrize(
    ("data", "said"),
    [
        (wda_archive((f"{FOLDER}/../evil", b"x")), "holds 'WebDriverAgent-"),
        (wda_archive(("/etc/evil", b"x")), "holds '/etc/evil'"),
        (wda_archive(("elsewhere/file", b"x")), "holds 'elsewhere/file'"),
        (wda_archive((FOLDER, None), link="/etc/passwd"), "/escape', which is not unpacked"),
        (b"not an archive", "could not be unpacked"),
        (wda_archive((FOLDER, None), (f"{FOLDER}/README.md", b"no project")), f"holds no {PROJECT}"),
        (wda_archive((FOLDER, None), (f"{FOLDER}/{PROJECT}", None)), "FBWebServer.m is not as SimMirror knows it"),
    ],
)
def test_an_archive_with_anything_outside_its_folder_or_not_a_file_is_refused_whole(
    tmp_path: Path, data: bytes, said: str
) -> None:
    with pytest.raises(WdaSourceError, match=said.replace(".", r"\.")):
        wda_source(tmp_path, release(data), fetch=Fetches(data))
    assert not (tmp_path / "etc").exists() and not (tmp_path / "evil").exists()


def test_a_checkout_of_the_persons_own_must_hold_the_project_with_the_stream_kept_on_the_device(
    tmp_path: Path,
) -> None:
    (tmp_path / PROJECT).mkdir()
    with pytest.raises(WdaSourceError, match="does not keep the screen stream on the device's loopback"):
        configured(str(tmp_path))
    server = tmp_path / MJPEG_ON_LOOPBACK.path
    server.parent.mkdir(parents=True)
    server.write_bytes(SERVER)
    patched(tmp_path)
    assert configured(str(tmp_path)) == tmp_path
    patched(tmp_path)
    assert server.read_text().count("SimMirror:") == 1, "a change is made once"
    with pytest.raises(WdaSourceError, match=f"holds no {PROJECT}"):
        configured(str(tmp_path / "elsewhere"))


class Answer(io.BytesIO):
    def __enter__(self) -> Answer:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def test_a_download_is_bounded_and_says_why_it_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[tuple[str, Any]] = []

    def urlopen(url: str, timeout: float) -> Answer:
        opened.append((url, timeout))
        if "fails" in url:
            raise OSError("no route to host")
        return Answer(b"x" * (8 if "small" in url else 20))

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(module, "ARCHIVE_MAX", 10)
    assert download("https://example.test/small") == b"x" * 8 and opened[0][1] == module.FETCH_TIMEOUT_S
    with pytest.raises(WdaSourceError, match="is larger than 0 MB"):
        download("https://example.test/large")
    with pytest.raises(WdaSourceError, match=r"could not be fetched from https://example.test/fails: no route"):
        download("https://example.test/fails")
