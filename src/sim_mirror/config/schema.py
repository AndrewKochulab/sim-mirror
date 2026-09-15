# SPDX-License-Identifier: Apache-2.0
"""Every setting SimMirror has, once: its key, where it lives in config.toml, its default, its rule and what it does.

Everything else about configuration is derived from this table rather than written again: the flat values a host
embedding SimMirror keeps (`defaults`, `errors`), the nested `config.toml` a standalone install reads (`flatten`,
`nested`), the ``SIM_MIRROR_*`` environment variables (`Setting.env`), `sim-mirror config`, and the generated
reference in `docs/reference/configuration.md`.

Each value is checked on its own, so a value its rule refuses reads as its default (`SimConfig.from_flat`) instead of
breaking the rest, and the refusal reads the same wherever it is reported.

Two profiles differ only in defaults: ``standalone`` -- the `sim-mirror` daemon, on, build tools off -- and
``embedded`` -- a host application, off until the host turns it on, build tools available where the host allows
commands.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

Profile = Literal["standalone", "embedded"]
PROFILES: tuple[Profile, ...] = ("standalone", "embedded")

PATH_MAX = 500
NAME_MAX = 100
ORIGINS_MAX = 20
ENV_PREFIX = "SIM_MIRROR_"
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})
_CONFIGURATION_NAME = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}\Z")
_CONNECTOR_NAME = re.compile(r"\A[a-z][a-z0-9_-]{0,31}\Z")
_ORIGIN = re.compile(r"\Ahttps?://(?:[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*|\[[0-9A-Fa-f:]+\])(?::\d{1,5})?\Z")


def _one_line(value: Any, limit: int) -> bool:
    return isinstance(value, str) and len(value) <= limit and not any(ch in value for ch in "\n\r\x00")


class Rule(Protocol):
    """What a setting's value must be."""

    def errors(self, name: str, value: Any) -> list[str]:
        """What is wrong with `value`, said with the setting's `name`; empty when nothing is."""
        ...

    def parse(self, raw: str) -> Any:
        """The value text stands for -- from an environment variable or the command line. Raises ValueError."""
        ...

    def describe(self) -> str:
        """The values the rule allows, for the reference."""
        ...


@dataclass(frozen=True)
class Flag:
    def errors(self, name: str, value: Any) -> list[str]:
        return [] if isinstance(value, bool) else [f"{name} must be true or false"]

    def parse(self, raw: str) -> bool:
        text = raw.strip().lower()
        if text in _TRUE or text in _FALSE:
            return text in _TRUE
        raise ValueError("must be true or false")

    def describe(self) -> str:
        return "`true` or `false`"


@dataclass(frozen=True)
class Whole:
    low: int
    high: int

    def errors(self, name: str, value: Any) -> list[str]:
        whole = isinstance(value, int) and not isinstance(value, bool)
        if whole and self.low <= value <= self.high:
            return []
        return [f"{name} must be a whole number between {self.low} and {self.high}"]

    def parse(self, raw: str) -> int:
        try:
            return int(raw.strip())
        except ValueError:
            raise ValueError(f"must be a whole number between {self.low} and {self.high}") from None

    def describe(self) -> str:
        return f"a whole number from {self.low} to {self.high}"


@dataclass(frozen=True)
class Choice:
    options: tuple[str, ...]

    def errors(self, name: str, value: Any) -> list[str]:
        if isinstance(value, str) and value in self.options:
            return []
        return [f"{name} must be one of: {', '.join(self.options)}"]

    def parse(self, raw: str) -> str:
        return raw.strip()

    def describe(self) -> str:
        return "one of " + ", ".join(f"`{option}`" for option in self.options)


@dataclass(frozen=True)
class ConnectorName:
    """``auto``, or the name of a connector: a built-in one, or one an installed package registers."""

    def errors(self, name: str, value: Any) -> list[str]:
        if isinstance(value, str) and (value == "auto" or _CONNECTOR_NAME.match(value)):
            return []
        return [f"{name} must be auto or a connector's name, such as idb or simctl"]

    def parse(self, raw: str) -> str:
        return raw.strip()

    def describe(self) -> str:
        return "`auto`, `idb`, `simctl`, or an installed connector's name"


@dataclass(frozen=True)
class AbsolutePath:
    def errors(self, name: str, value: Any) -> list[str]:
        if not _one_line(value, PATH_MAX):
            return [f"{name} must be one line of at most {PATH_MAX} characters"]
        return [] if not value or value.startswith("/") else [f"{name} must be an absolute path"]

    def parse(self, raw: str) -> str:
        return raw.strip()

    def describe(self) -> str:
        return "an absolute path, or empty"


@dataclass(frozen=True)
class Name:
    required: bool = False

    def errors(self, name: str, value: Any) -> list[str]:
        if not _one_line(value, NAME_MAX):
            return [f"{name} must be one line of at most {NAME_MAX} characters"]
        return [f"{name} must not be empty"] if self.required and not value.strip() else []

    def parse(self, raw: str) -> str:
        return raw.strip()

    def describe(self) -> str:
        return f"text of at most {NAME_MAX} characters" + ("" if self.required else ", or empty")


@dataclass(frozen=True)
class ConfigurationName:
    def errors(self, name: str, value: Any) -> list[str]:
        if isinstance(value, str) and _CONFIGURATION_NAME.match(value):
            return []
        return [f"{name} must be a build configuration name, such as Debug or Release"]

    def parse(self, raw: str) -> str:
        return raw.strip()

    def describe(self) -> str:
        return "a build configuration name, such as `Debug` or `Release`"


@dataclass(frozen=True)
class Origins:
    def errors(self, name: str, value: Any) -> list[str]:
        valid = (
            isinstance(value, (list, tuple))
            and len(value) <= ORIGINS_MAX
            and all(isinstance(origin, str) and _ORIGIN.match(origin) for origin in value)
        )
        return (
            [] if valid else [f"{name} must be a list of at most {ORIGINS_MAX} origins, such as http://localhost:3000"]
        )

    def parse(self, raw: str) -> tuple[str, ...]:
        return tuple(part.strip() for part in raw.split(",") if part.strip())

    def describe(self) -> str:
        return f"a list of up to {ORIGINS_MAX} origins, such as `http://localhost:3000`"


@dataclass(frozen=True)
class LoopbackHost:
    def errors(self, name: str, value: Any) -> list[str]:
        return [] if value in LOOPBACK_HOSTS else [f"{name} must be a loopback address: {', '.join(LOOPBACK_HOSTS)}"]

    def parse(self, raw: str) -> str:
        return raw.strip()

    def describe(self) -> str:
        return "a loopback address: " + ", ".join(f"`{host}`" for host in LOOPBACK_HOSTS)


class _Same:
    def __repr__(self) -> str:
        return "the standalone default"


SAME: Any = _Same()


@dataclass(frozen=True)
class Setting:
    #: The flat key: a `SimConfig` attribute, and the key a host keeps the value under.
    key: str
    #: Where the value lives in config.toml: ``stream.fps``, or ``enabled`` at the top.
    path: str
    default: Any
    rule: Rule
    doc: str
    #: The default a host embedding SimMirror starts from, when it is not the standalone one.
    embedded_default: Any = SAME

    def default_for(self, profile: Profile) -> Any:
        return self.default if profile == "standalone" or self.embedded_default is SAME else self.embedded_default

    @property
    def env(self) -> str:
        """The environment variable that overrides it: ``SIM_MIRROR_`` and its path, upper-cased, with underscores."""
        return ENV_PREFIX + self.path.upper().replace(".", "_")

    def errors(self, value: Any, *, name: str | None = None) -> list[str]:
        return self.rule.errors(name or self.path, value)


SETTINGS: tuple[Setting, ...] = (
    Setting("enabled", "enabled", True, Flag(),
            "Whether devices are booted and connectors started at all. Turning it off stops them.",
            embedded_default=False),
    Setting("connector", "connectors.preferred", "auto", ConnectorName(),
            "Which connector drives devices. `auto` uses idb when idb_companion is installed and falls back to simctl, "
            "which can only show the screen."),
    Setting("companion_path", "connectors.idb.companion_path", "", AbsolutePath(),
            "The idb_companion to run. Empty: the one on PATH, else where Homebrew installs it."),
    Setting("developer_dir", "device.developer_dir", "", AbsolutePath(),
            "The Xcode to use, as its Contents/Developer folder. Empty: the one `xcode-select` names."),
    Setting("device_type", "device.type", "", Name(),
            "The device type to create, such as iPhone 17 Pro. Empty: the newest iPhone the runtime offers."),
    Setting("runtime", "device.runtime", "", Name(),
            "The runtime to create devices with, such as iOS 26.5. Empty: the newest iOS runtime installed."),
    Setting("device_mode", "device.mode", "per_scope", Choice(("per_scope", "shared")),
            "`per_scope` gives every scope a device of its own, so agents never tap on each other's apps; `shared` "
            "gives a whole group one device."),
    Setting("device_name_prefix", "device.name_prefix", "SimMirror", Name(required=True),
            "How devices SimMirror creates are named in Xcode's device list: the prefix, then the scope."),
    Setting("max_booted", "device.max_booted", 2, Whole(1, 8),
            "How many devices are kept booted at once. Each takes 2-3 GB of memory."),
    Setting("idle_minutes", "device.idle_minutes", 15, Whole(5, 240),
            "A device nobody watches, no agent holds and nothing builds on is stopped after this many minutes."),
    Setting("shutdown_on_idle", "device.shutdown_on_idle", True, Flag(),
            "Whether stopping an idle device also shuts it down, when SimMirror booted or created it."),
    Setting("stream_encoding", "stream.encoding", "auto", Choice(("auto", "jpeg", "h264")),
            "How the screen is streamed. `auto` is H.264 where the viewer can decode it and JPEG where it cannot."),
    Setting("stream_fps", "stream.fps", 30, Whole(5, 60), "Frames per second the screen is streamed at."),
    Setting("stream_quality", "stream.quality", 75, Whole(30, 100), "JPEG quality, in percent."),
    Setting("stream_max_width", "stream.max_width", 900, Whole(320, 1600),
            "The widest a streamed frame is, in pixels."),
    Setting("agent_tools", "agent.tools", True, Flag(), "Whether agents are offered the simulator's MCP tools."),
    Setting("agent_cursor", "agent.cursor", True, Flag(),
            "Whether viewers draw the agent's pointer, touches and trails."),
    Setting("cursor_lead_ms", "agent.cursor_lead_ms", 250, Whole(0, 1000),
            "How long the agent's pointer takes to reach a point before the touch lands, so a person watching sees "
            "where it goes first. Only while someone watches."),
    Setting("screenshot_width", "agent.screenshot_width", 400, Whole(160, 1200),
            "How wide a screenshot an agent gets back, in pixels."),
    Setting("snapshot_max_elements", "agent.snapshot_max_elements", 120, Whole(20, 400),
            "The most elements an agent's snapshot of the screen lists."),
    Setting("build_tools", "build.tools", False, Flag(),
            "Whether agents are offered `sim_build_run` and `sim_test` (preview). They run xcodebuild, so a host "
            "also has to allow commands.",
            embedded_default=True),
    Setting("build_configuration", "build.configuration", "Debug", ConfigurationName(),
            "The build configuration used when a call names none."),
    Setting("build_timeout_minutes", "build.timeout_minutes", 20, Whole(1, 60),
            "A build or test run longer than this is stopped."),
    Setting("server_host", "server.host", "127.0.0.1", LoopbackHost(),
            "The address the daemon listens on. Loopback only in this version."),
    Setting("server_port", "server.port", 7466, Whole(1024, 65535), "The port the daemon listens on."),
    Setting("allowed_origins", "security.allowed_origins", (), Origins(),
            "Web origins, besides the daemon's own, that may call its API and open screen sockets."),
    Setting("frame_ancestors", "security.frame_ancestors", (), Origins(),
            "Origins, besides the daemon's own, allowed to show the viewer in an iframe."),
)  # fmt: skip

BY_KEY: Mapping[str, Setting] = {setting.key: setting for setting in SETTINGS}
BY_PATH: Mapping[str, Setting] = {setting.path: setting for setting in SETTINGS}


def find(name: str) -> Setting | None:
    """A setting by its flat key or its config.toml path."""
    return BY_KEY.get(name) or BY_PATH.get(name)


def defaults(profile: Profile = "standalone") -> dict[str, Any]:
    """Every setting's default for a profile, by flat key."""
    return {setting.key: setting.default_for(profile) for setting in SETTINGS}


def path_name(setting: Setting) -> str:
    return setting.path


def errors(
    values: Mapping[str, Any],
    *,
    naming: Callable[[Setting], str] = path_name,
    unknown: str = "unknown settings",
) -> list[str]:
    """Everything wrong with flat values: keys that are no setting, then each value its rule refuses."""
    found: list[str] = []
    strangers = sorted(set(values) - set(BY_KEY))
    if strangers:
        found.append(f"{unknown}: {', '.join(strangers)}")
    for setting in SETTINGS:
        if setting.key in values:
            found.extend(setting.errors(values[setting.key], name=naming(setting)))
    return found


def nested(values: Mapping[str, Any]) -> dict[str, Any]:
    """Flat values as config.toml nests them."""
    document: dict[str, Any] = {}
    for key, value in values.items():
        *tables, leaf = BY_KEY[key].path.split(".")
        node = document
        for table in tables:
            node = node.setdefault(table, {})
        node[leaf] = list(value) if isinstance(value, tuple) else value
    return document


def flatten(document: Mapping[str, Any], *, skip: frozenset[str] = frozenset()) -> tuple[dict[str, Any], list[str]]:
    """A config.toml document's values by flat key, and the paths in it that are no setting.

    Top-level tables named in `skip` are left out: they are read separately (the per-scope overrides).
    """
    values: dict[str, Any] = {}
    unknown: list[str] = []

    def walk(node: Mapping[str, Any], prefix: str) -> None:
        for name, value in node.items():
            path = f"{prefix}{name}"
            setting = BY_PATH.get(path)
            if not prefix and name in skip:
                continue
            if setting is not None:
                values[setting.key] = tuple(value) if isinstance(value, list) else value
            elif isinstance(value, Mapping) and any(known.startswith(f"{path}.") for known in BY_PATH):
                walk(value, f"{path}.")
            else:
                unknown.append(path)

    walk(document, "")
    return values, unknown
