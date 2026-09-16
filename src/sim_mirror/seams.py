# SPDX-License-Identifier: Apache-2.0
"""The seams between SimMirror and whatever runs it: the few things a host decides.

SimMirror's core boots devices, streams them and plays gestures. What it must *ask* is here, as protocols a host
implements -- the standalone daemon's implementations are in `sim_mirror.daemon`, and a host embedding SimMirror
passes its own to `sim_mirror.api.Runtime.build`:

* `ConfigSource` -- a scope's settings, read on every operation so a change applies at once;
* `StateStore` -- where builds are kept, where sockets, logs and claims live, and who owns them;
* `DeviceMemory` -- which simulator a scope uses, remembered between runs. `JsonDeviceMemory` is the one
  SimMirror ships, over a file; a host with somewhere better to put it -- a database row -- passes its own;
* `UsageProbe` -- whether an agent holds a device, so it is not reaped under the agent;
* `Policy` -- whether a scope may have a simulator, run commands, and install from which folders;
* `Authenticator` -- who is making a request: a person, an agent, or a screen socket.

A host that lets a page change settings -- the viewer's settings panel, `server.settings_routes` -- also implements
`SettingsStore` (where each value comes from, and writing a change), `SettingsAuthenticator` (what the asker may do)
and, for the settings a page may not change alone, `Confirmations`. A host that keeps its own settings screens
implements none of them and does not mount that router.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from sim_mirror.scope import Scope

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.websockets import WebSocket

    from sim_mirror.config.model import SimConfig
    from sim_mirror.config.provenance import SettingOrigin
    from sim_mirror.protocol import PendingConfirmation


class ConfigSource(Protocol):
    def get(self, scope: Scope) -> SimConfig:
        """The scope's effective settings now. Called on every operation, so it should be cheap."""
        ...


class StateStore(Protocol):
    @property
    def owner_tag(self) -> str:
        """Names this host in pid files and device claims, so one host never reaps another's companions."""
        ...

    def builds_dir(self, scope: Scope) -> Path:
        """Where a scope's build logs and result bundles go."""
        ...

    def derived_data(self, scope: Scope) -> Path:
        """The DerivedData folder a scope's builds use."""
        ...

    def run_dir(self) -> Path:
        """Where companion sockets and pid files live. Keep it short: a unix socket's path may have 104 bytes."""
        ...

    def log_dir(self) -> Path: ...

    def claims_dir(self) -> Path:
        """Where device claims live. Every host on the Mac should share it, so they see each other's claims."""
        ...

    def ensure_dir(self, folder: Path) -> Path:
        """Make a folder for SimMirror's state, private to this user, and answer it."""
        ...


class DeviceMemory(Protocol):
    """Which device each scope uses, remembered by UDID -- never by name, which a person can change and two devices
    can share. `shared` asks for the device a whole group shares (``device.mode = "shared"``)."""

    def assigned(self, scope: Scope, shared: bool) -> str | None:
        """The UDID remembered for the scope, whether or not that device still exists."""
        ...

    def created(self, scope: Scope) -> set[str]:
        """The UDIDs of devices SimMirror created for this scope's host, which it may delete when asked."""
        ...

    def choose(self, scope: Scope, shared: bool, udid: str) -> None:
        """Remember a device a person picked."""
        ...

    def forget(self, scope: Scope, shared: bool) -> str | None:
        """Stop remembering the scope's device; answers the UDID it had."""
        ...

    def remember_created(self, scope: Scope, shared: bool, udid: str) -> None:
        """Remember a device SimMirror just created for the scope."""
        ...


class HeldDevice(Protocol):
    """What a usage probe is told about a device."""

    @property
    def udid(self) -> str: ...

    @property
    def group(self) -> str: ...

    @property
    def scopes(self) -> Collection[str]:
        """The ids of the scopes using the device."""
        ...


class UsageProbe(Protocol):
    def in_use(self, device: HeldDevice) -> bool:
        """Whether an agent holds the device right now, so it must not be reaped for being idle."""
        ...


class Policy(Protocol):
    def area_enabled(self, scope: Scope) -> bool:
        """Whether the host offers a simulator to this scope at all."""
        ...

    def shells_allowed(self, scope: Scope) -> bool:
        """Whether this scope may run commands: builds and tests need it."""
        ...

    def install_roots(self, scope: Scope) -> tuple[Path, ...]:
        """The folders an app may be installed from."""
        ...

    def build_folder(self, scope: Scope) -> Path | None:
        """The folder a build looks for its Xcode project in; None where this scope builds nothing."""
        ...


@dataclass(frozen=True)
class Person:
    """A person using a viewer."""

    scope: Scope


@dataclass(frozen=True)
class Caller:
    """An agent calling a tool: its scope, a key that stays the same for its connection, and the title viewers show."""

    scope: Scope
    key: str
    title: str


@dataclass(frozen=True)
class Admission:
    """A screen socket let in, and the scope its ticket opens."""

    scope: Scope


class Refused(Exception):
    """A request that is not let in, with the HTTP status (or WebSocket close code) and a message saying why."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class Authenticator(Protocol):
    async def person(self, request: Request, scope_id: str) -> Person:
        """The person behind an HTTP request for a scope. Raises `Refused`."""
        ...

    async def agent(self, request: Request) -> Caller:
        """The agent behind a tool call. Raises `Refused`."""
        ...

    async def admit_socket(self, websocket: WebSocket, scope_id: str) -> Admission:
        """Whether a screen socket for a scope may be accepted, before its ticket is read. Raises `Refused` with a
        WebSocket close code."""
        ...


@dataclass(frozen=True)
class SettingsEditor:
    """What a person asking about a scope's settings may do: read them, change them, and change the sensitive ones
    without a person confirming at the terminal."""

    scope: Scope
    may_write: bool = False
    may_write_sensitive: bool = False


#: What a settings refusal says when it names no setting's problem.
SETTINGS_REFUSED = "the change was refused"


class SettingsRefused(Exception):
    """A settings change refused before anything was written, with what is wrong with each setting, by path."""

    def __init__(self, errors: Mapping[str, str]) -> None:
        super().__init__(next(iter(errors.values()), SETTINGS_REFUSED))
        self.errors = dict(errors)


class SettingsAuthenticator(Protocol):
    async def settings_editor(self, request: Request, scope_id: str) -> SettingsEditor:
        """What the asker may do with a scope's settings. Raises `Refused` for someone who may not even read them."""
        ...


class SettingsStore(Protocol):
    def explain(self, scope: Scope) -> Mapping[str, SettingOrigin]:
        """Where each value the scope sees comes from, by flat key."""
        ...

    def change(self, scope: Scope | None, values: Mapping[str, Any], removed: Collection[str]) -> None:
        """Set values and put settings back, by path, for one scope or -- None -- every scope; all or none. Raises
        `SettingsRefused`. It blocks: the router runs it in a thread."""
        ...


class Confirmations(Protocol):
    """Sensitive changes waiting for a person to confirm them somewhere a page cannot reach, such as the terminal."""

    def request(self, scope: Scope, digest: str, summary: str) -> PendingConfirmation:
        """Hold a change -- known by its digest -- until it is confirmed; asked again for the same one, the same."""
        ...

    def confirm(self, digest: str, code: str) -> bool:
        """Whether the code confirms that change, spending it when it does."""
        ...
