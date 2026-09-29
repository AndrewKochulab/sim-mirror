# SPDX-License-Identifier: Apache-2.0
"""WebDriverAgent set up for a signing team and an Xcode: the pinned release fetched and checked (`wda_source`), its
screen stream kept on the device, and built for a device, which Xcode registers with the team (`build.wda`).

A person asks for it the first time -- the viewer's Set up touch, or ``sim-mirror wda setup``. After that it is
SimMirror's to redo: a team with a build has a person's yes, so a new Xcode, or a device its build was not made for, is
built for by itself when a session needs it (`WdaService.client`). One setup runs per team and Xcode at a time, and what
it came to is kept until the next is asked for, so a failure is shown rather than tried again and again.

Which devices a build was made for is written beside it (`DEVICES_FILE`): a build's profile covers the devices its team
had when it was made, and a build for a device registers that device first.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from sim_mirror.build.wda import build_wda, xctestrun
from sim_mirror.connectors.iphone import wda_source as releases
from sim_mirror.connectors.iphone.wda_source import Fetch, WdaRelease, WdaSourceError, configured, download
from sim_mirror.platform.xcrun import XcrunRunner, run_xcrun
from sim_mirror.storage import app_support

logger = logging.getLogger(__name__)

SetupState = Literal["building", "ready", "failed"]
#: The devices a build was made for, beside it.
DEVICES_FILE = "simmirror-devices.json"


def wda_root(env: Mapping[str, str] = os.environ) -> Path:
    """Where WebDriverAgent's source and builds are kept."""
    return app_support.state_dir(env) / "wda"


def derived_for(root: Path, team: str, developer_dir: str) -> Path:
    """Where WebDriverAgent is built for a team with an Xcode, from the source SimMirror pins and the changes it makes
    to it: a build of one is not another's, so a change to either is built again -- by itself, once a person set it up
    for the team."""
    xcode = hashlib.sha256(developer_dir.encode()).hexdigest()[:8]
    source = hashlib.sha256(releases.stamp(releases.PINNED).encode()).hexdigest()[:8]
    return root / "derived" / f"{team}-{xcode}-{source}"


def devices_built_for(derived: Path) -> set[str]:
    try:
        listed = json.loads((derived / DEVICES_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {str(udid) for udid in listed} if isinstance(listed, list) else set()


@dataclass
class Setup:
    """One setup for a team and an Xcode: under way, done, or failed and why."""

    team: str
    xcode: str
    #: The device it is built for; None for any device the team's profile covers.
    udid: str | None
    state: SetupState = "building"
    #: What it is doing now, or why it failed.
    said: str = ""
    task: asyncio.Task[None] | None = field(default=None, repr=False)


class WdaSetup:
    """Sets WebDriverAgent up, and says how each setup stands. The keyword arguments are test seams."""

    def __init__(
        self,
        *,
        root: Callable[[], Path] = wda_root,
        fetch: Fetch = download,
        xcrun: XcrunRunner = run_xcrun,
        release: WdaRelease | None = None,
    ) -> None:
        self._root = root
        #: The release fetched: the one SimMirror pins.
        self._release = release or releases.PINNED
        self._fetch = fetch
        self._xcrun = xcrun
        self._setups: dict[tuple[str, str], Setup] = {}
        #: Told the device a setup built for, once it is done, so a session waiting for it can start it.
        self.on_ready: Callable[[str], object] | None = None

    def built_for(self, team: str, xcode: str, udid: str) -> Path | None:
        """What to run WebDriverAgent on the device from, when a build for the team and Xcode was made for it."""
        derived = derived_for(self._root(), team, xcode)
        run = xctestrun(derived)
        return run if run is not None and udid in devices_built_for(derived) else None

    def agreed(self, team: str) -> bool:
        """Whether a person has set WebDriverAgent up for the team before, with any Xcode."""
        return any(xctestrun(folder) is not None for folder in (self._root() / "derived").glob(f"{team}-*"))

    def state(self, team: str, xcode: str) -> Setup | None:
        return self._setups.get((team, xcode))

    def start(
        self, team: str, xcode: str, udid: str | None, source_path: str = "", say: Callable[[str], None] | None = None
    ) -> Setup:
        """Set WebDriverAgent up for the team and Xcode, for the device named -- or answer the setup under way."""
        known = self._setups.get((team, xcode))
        if known is not None and known.state == "building":
            return known
        setup = Setup(team, xcode, udid)
        self._setups[(team, xcode)] = setup
        setup.task = asyncio.get_running_loop().create_task(self._run(setup, source_path, say))
        return setup

    async def _run(self, setup: Setup, source_path: str, say: Callable[[str], None] | None) -> None:
        try:
            await self._steps(setup, source_path, say)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # a setup no one awaits must still end, and say why
            self._failed(setup, f"{type(exc).__name__}: {exc}")

    async def _steps(self, setup: Setup, source_path: str, say: Callable[[str], None] | None) -> None:
        def step(text: str) -> None:
            setup.said = text
            if say is not None:
                say(text)

        root = self._root()
        try:
            if source_path:
                source = configured(source_path)
            else:
                release = self._release
                if not (root / "source" / release.folder).is_dir():
                    step(f"fetching WebDriverAgent {release.version} ({release.commit[:12]}) and checking its SHA-256")
                source = await asyncio.to_thread(releases.wda_source, root / "source", release, fetch=self._fetch)
        except WdaSourceError as exc:
            self._failed(setup, str(exc))
            return
        target = setup.udid or "any device the team's profile covers"
        step(f"building WebDriverAgent for {target} with team {setup.team}; a first build takes a minute or two")
        derived = derived_for(root, setup.team, setup.xcode)
        built = await build_wda(source, derived, setup.team, setup.xcode, udid=setup.udid, xcrun=self._xcrun)
        if built.xctestrun is None:
            self._failed(setup, built.failure)
            return
        if setup.udid is not None:
            listed = sorted(devices_built_for(derived) | {setup.udid})
            (derived / DEVICES_FILE).write_text(json.dumps(listed), encoding="utf-8")
        setup.state, setup.said = "ready", f"WebDriverAgent is built for team {setup.team}"
        logger.info("WebDriverAgent is set up for team %s (%s)", setup.team, target)
        if setup.udid is not None and self.on_ready is not None:
            self.on_ready(setup.udid)

    @staticmethod
    def _failed(setup: Setup, why: str) -> None:
        setup.state, setup.said = "failed", why
        logger.warning("WebDriverAgent could not be set up for team %s: %s", setup.team, why)

    async def wait(self, setup: Setup) -> Setup:
        """The setup once it is done or has failed."""
        if setup.task is not None:
            await asyncio.shield(setup.task)
        return setup

    async def shutdown(self) -> None:
        for setup in self._setups.values():
            if setup.task is not None and not setup.task.done():
                setup.task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await setup.task
