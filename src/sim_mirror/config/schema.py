# SPDX-License-Identifier: Apache-2.0
"""Every setting SimMirror has, once: its key, where it lives in config.toml, its default, its rule and what it does.

Everything else about configuration is derived from this table rather than written again: the flat values a host
embedding SimMirror keeps (`defaults`, `errors`), the nested `config.toml` a standalone install reads (`flatten`,
`nested`), the ``SIM_MIRROR_*`` environment variables (`Setting.env`), `sim-mirror config`, the viewer's settings panel
(`Section`, `Rule.spec`, and when a change takes effect, for whom, and whether a page may make it alone), and the
generated reference in `docs/reference/configuration.md`.

Each value is checked on its own, so a value its rule refuses reads as its default (`SimConfig.from_flat`) instead of
breaking the rest, and the refusal reads the same wherever it is reported.

Two profiles differ only in defaults: ``standalone`` -- the `sim-mirror` daemon, on, build tools off -- and
``embedded`` -- a host application, off until the host turns it on, build tools available where the host allows
commands.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from sim_mirror.validation import XCODE_NAME_MAX, is_xcode_name

Profile = Literal["standalone", "embedded"]
PROFILES: tuple[Profile, ...] = ("standalone", "embedded")
#: When a change takes effect: at once, on a screen's next connection, on the device next brought up, or when the
#: daemon restarts.
Effect = Literal["live", "next_connection", "next_device", "restart"]
#: Whom a setting is for: each scope, which may have its own value, or the whole daemon, which reads only its own.
Reach = Literal["scope", "global"]

PATH_MAX = 500
NAME_MAX = 100
ORIGINS_MAX = 20
CONNECTOR_NAME_MAX = 32
LANGUAGES_MAX = 100
ENV_PREFIX = "SIM_MIRROR_"
#: What `server.host` may be: the address the command line reaches the daemon on (`daemon.lifecycle.LOOPBACK`).
LOOPBACK_HOSTS = ("127.0.0.1",)

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})
_CONNECTOR_NAME = re.compile(rf"\A[a-z][a-z0-9_-]{{0,{CONNECTOR_NAME_MAX - 1}}}\Z")
#: A BCP 47 language code as Vision takes one: a language, then any script or region -- ``en``, ``en-US``, ``zh-Hans``.
_LANGUAGE = re.compile(r"\A[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*\Z")
_ORIGIN = re.compile(r"\Ahttps?://(?:[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*|\[[0-9A-Fa-f:]+\])(?::\d{1,5})?\Z")


def _text(format_: str, max_length: int, *, required: bool, example: str) -> dict[str, Any]:
    return {
        "kind": "text",
        "format": format_,
        "max_length": max_length,
        "required": required,
        "example": example,
        "suggestions": None,
    }


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

    def spec(self) -> dict[str, Any]:
        """The values the rule allows, as a form needs them: the protocol's ``RuleSpec``, told apart by ``kind``."""
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

    def spec(self) -> dict[str, Any]:
        return {"kind": "flag"}


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

    def spec(self) -> dict[str, Any]:
        return {"kind": "whole", "low": self.low, "high": self.high}


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

    def spec(self) -> dict[str, Any]:
        return {"kind": "choice", "options": list(self.options)}


@dataclass(frozen=True)
class ConnectorName:
    """``auto``, or the name of a connector: a built-in one, or one an installed package registers."""

    def errors(self, name: str, value: Any) -> list[str]:
        if isinstance(value, str) and (value == "auto" or _CONNECTOR_NAME.match(value)):
            return []
        return [f"{name} must be auto or a connector's name, such as native, idb or simctl"]

    def parse(self, raw: str) -> str:
        return raw.strip()

    def describe(self) -> str:
        return "`auto`, `native`, `idb`, `simctl`, `mcpbridge`, or an installed connector's name"

    def spec(self) -> dict[str, Any]:
        return _text("connector", CONNECTOR_NAME_MAX, required=True, example="auto")


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

    def spec(self) -> dict[str, Any]:
        return _text("path", PATH_MAX, required=False, example="/Applications/Xcode.app/Contents/Developer")


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

    def spec(self) -> dict[str, Any]:
        return _text("name", NAME_MAX, required=self.required, example="")


@dataclass(frozen=True)
class ConfigurationName:
    def errors(self, name: str, value: Any) -> list[str]:
        if is_xcode_name(value):
            return []
        return [f"{name} must be a build configuration name as Xcode shows it, such as Debug or Release"]

    def parse(self, raw: str) -> str:
        return raw.strip()

    def describe(self) -> str:
        return "a build configuration name, such as `Debug` or `Release`"

    def spec(self) -> dict[str, Any]:
        return _text("configuration", XCODE_NAME_MAX, required=True, example="Debug")


@dataclass(frozen=True)
class Origins:
    def errors(self, name: str, value: Any) -> list[str]:
        valid = (
            isinstance(value, (list, tuple))
            and len(value) <= ORIGINS_MAX
            and all(isinstance(origin, str) and _ORIGIN.match(origin) for origin in value)
        )
        refusal = (
            f"{name} must be a list of at most {ORIGINS_MAX} origins, such as http://localhost:3000;"
            " separate several with commas"
        )
        return [] if valid else [refusal]

    def parse(self, raw: str) -> tuple[str, ...]:
        """Origins separated by commas, or the JSON list `sim-mirror config get` prints."""
        parts: Sequence[object] = raw.split(",")
        if raw.strip().startswith("["):
            with suppress(ValueError):  # JSON text that starts with "[" is a list, or not JSON at all
                parts = json.loads(raw)
        return tuple(text for text in (str(part).strip() for part in parts) if text)

    def describe(self) -> str:
        return f"a list of up to {ORIGINS_MAX} origins, such as `http://localhost:3000`"

    def spec(self) -> dict[str, Any]:
        return {"kind": "origins", "max_items": ORIGINS_MAX}


def language_codes(value: str) -> tuple[str, ...]:
    """The language codes a `Languages` value names, in its order."""
    return tuple(code for code in (part.strip() for part in value.split(",")) if code)


@dataclass(frozen=True)
class Languages:
    """Language codes, most likely first, separated by commas: ``en-US, uk-UA``. Empty for whichever is detected."""

    def errors(self, name: str, value: Any) -> list[str]:
        if _one_line(value, LANGUAGES_MAX) and all(_LANGUAGE.match(code) for code in language_codes(value)):
            return []
        return [
            f"{name} must be language codes such as en-US, separated by commas, in at most {LANGUAGES_MAX} characters"
        ]

    def parse(self, raw: str) -> str:
        return raw.strip()

    def describe(self) -> str:
        return "language codes such as `en-US`, separated by commas, or empty"

    def spec(self) -> dict[str, Any]:
        return _text("languages", LANGUAGES_MAX, required=False, example="en-US, uk-UA")


@dataclass(frozen=True)
class LoopbackHost:
    def errors(self, name: str, value: Any) -> list[str]:
        return [] if value in LOOPBACK_HOSTS else [f"{name} must be a loopback address: {', '.join(LOOPBACK_HOSTS)}"]

    def parse(self, raw: str) -> str:
        return raw.strip()

    def describe(self) -> str:
        return "a loopback address: " + ", ".join(f"`{host}`" for host in LOOPBACK_HOSTS)

    def spec(self) -> dict[str, Any]:
        return {"kind": "choice", "options": list(LOOPBACK_HOSTS)}


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
    #: When a change takes effect.
    effect: Effect = "live"
    #: Whether a scope may have its own value, or only the whole daemon's counts.
    reach: Reach = "scope"
    #: Whether it decides what SimMirror runs or who may reach it -- a program, an Xcode, commands, origins, the port --
    #: so that a page may not change it without a person confirming at the terminal.
    sensitive: bool = False

    @property
    def section(self) -> str:
        """The top-level table it lives in -- one tab of the settings panel -- or empty at the top."""
        return self.path.split(".", 1)[0] if "." in self.path else ""

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
            "Which connector drives devices. `auto` uses the native helper, the fastest, falls back to idb when the "
            "helper cannot reach the device and idb_companion is installed, and to simctl, which can only show the "
            "screen. `mcpbridge` shows the screen and reads it through Xcode 27, without touching it, and is used "
            "only when named."),
    Setting("native_helper_path", "connectors.native.helper_path", "", AbsolutePath(),
            "The native helper to run. Empty: the one shipped with SimMirror, else the one `sim-mirror helper build` "
            "built.",
            effect="next_device", sensitive=True),
    Setting("native_hid_transport", "connectors.native.hid_transport", "auto", Choice(("auto", "dtuhid", "indigo")),
            "How the native helper sends touches, buttons and keys. `auto` uses dtuhid, the input service simulators "
            "run from Xcode 27's CoreSimulator on, and SimulatorKit's older Indigo messages where that is missing; "
            "`dtuhid` or `indigo` uses only that one.",
            effect="next_device"),
    Setting("native_startup_timeout", "connectors.native.startup_timeout", 15, Whole(3, 120),
            "How many seconds the native helper has to reach a device and open its screen before SimMirror gives up "
            "on it, and `auto` falls back to idb.",
            effect="next_device"),
    Setting("native_idle_key_frames", "connectors.native.idle_key_frames", True, Flag(),
            "Whether the native helper's H.264 stream sends a key frame every second while the screen is still, so a "
            "viewer that joins then sees the screen at once rather than when something next moves.",
            effect="next_device"),
    Setting("companion_path", "connectors.idb.companion_path", "", AbsolutePath(),
            "The idb_companion to run. Empty: the one on PATH, else where Homebrew installs it.",
            effect="next_device", sensitive=True),
    Setting("mcpbridge_merge", "connectors.mcpbridge.merge", False, Flag(),
            "Whether an agent's snapshots also read the screen through Xcode 27's UI hierarchy (mcpbridge) when "
            "another connector drives the device, adding what accessibility leaves out: a web page's text and links, "
            "a widget's text, the status bar. Each snapshot takes 0.2 to 0.9 seconds longer. Needs Xcode 27, and "
            "Xcode's approval (`sim-mirror xcode approve`)."),
    Setting("developer_dir", "device.developer_dir", "", AbsolutePath(),
            "The Xcode to use, as its Contents/Developer folder. Empty: the one `xcode-select` names.",
            sensitive=True),
    Setting("device_type", "device.type", "", Name(),
            "The device type to create, such as iPhone 17 Pro. Empty: the newest iPhone the runtime offers.",
            effect="next_device"),
    Setting("runtime", "device.runtime", "", Name(),
            "The runtime to create devices with, such as iOS 26.5. Empty: the newest iOS runtime installed.",
            effect="next_device"),
    Setting("device_mode", "device.mode", "per_scope", Choice(("per_scope", "shared")),
            "`per_scope` gives every scope a device of its own, so agents never tap on each other's apps; `shared` "
            "gives a whole group one device.",
            effect="next_device"),
    Setting("device_name_prefix", "device.name_prefix", "SimMirror", Name(required=True),
            "How devices SimMirror creates are named in Xcode's device list: the prefix, then the scope.",
            effect="next_device"),
    Setting("max_booted", "device.max_booted", 2, Whole(1, 8),
            "How many devices are kept booted at once. Each takes 2-3 GB of memory.",
            effect="next_device"),
    Setting("idle_minutes", "device.idle_minutes", 15, Whole(5, 240),
            "A device nobody watches, no agent holds and nothing builds on is stopped after this many minutes."),
    Setting("shutdown_on_idle", "device.shutdown_on_idle", True, Flag(),
            "Whether stopping an idle device also shuts it down, when SimMirror booted or created it."),
    Setting("device_typing", "device.typing", "auto", Choice(("auto", "keys", "paste")),
            "How text reaches the device. `auto` types it as key presses when every character is on a US keyboard "
            "and the Mac's keyboard layout is US or ABC, and pastes it otherwise; `keys` types whatever has keys, "
            "for a US-shaped layout SimMirror does not know; `paste` always pastes, keeping every character exact -- "
            "iOS 26 asks to Allow Paste, and iOS 27 refuses the paste.",
            effect="next_connection"),
    Setting("stream_encoding", "stream.encoding", "auto", Choice(("auto", "jpeg", "h264")),
            "How the screen is streamed. `auto` is H.264 where the viewer can decode it and JPEG where it cannot.",
            effect="next_connection"),
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
    Setting("cursor_linger_s", "agent.cursor_linger_s", 60, Whole(0, 3600),
            "How long the agent's pointer stays on a viewer's screen, resting where the agent last acted, after the "
            "agent's last tool call -- and for as long as one runs, a build or a test included. `0` lets it go once "
            "each gesture is drawn. It is drawn by the viewer, over the screen: never in a screenshot or recording "
            "of the device."),
    Setting("screenshot_width", "agent.screenshot_width", 400, Whole(160, 1200),
            "How wide a screenshot an agent gets back, in pixels."),
    Setting("snapshot_max_elements", "agent.snapshot_max_elements", 120, Whole(20, 400),
            "The most elements an agent's snapshot of the screen lists."),
    Setting("ocr_mode", "perception.ocr", "fallback", Choice(("off", "fallback", "merge")),
            "When an agent's snapshot reads the text in the screen's pixels, with macOS's Vision. `fallback` reads it "
            "when the accessibility tree says nothing -- an app still loading, a game, a canvas -- and lets a device "
            "whose connector cannot read the tree, such as simctl, be read at all; `merge` also adds the text the "
            "tree leaves out, on every snapshot, which takes 0.3 to 1 second more each; `off` never reads pixels. "
            "The first read compiles a small Swift helper with the scope's Xcode, once."),
    Setting("ocr_level", "perception.ocr_level", "accurate", Choice(("accurate", "fast")),
            "How carefully text is read from pixels: `accurate` reads small and stylised text better, `fast` takes a "
            "fraction of the time."),
    Setting("ocr_languages", "perception.ocr_languages", "", Languages(),
            "The languages text is read in, most likely first, as codes such as en-US or uk-UA separated by commas. "
            "Empty: whichever the text looks like."),
    Setting("ocr_correction", "perception.ocr_correction", True, Flag(),
            "Whether words read from pixels are corrected against a dictionary. Turn it off for codes, numbers and "
            "names that are not words."),
    Setting("ocr_min_confidence", "perception.ocr_min_confidence", 30, Whole(0, 100),
            "How sure, in percent, the reading must be of a line of text for a snapshot to list it."),
    Setting("ocr_timeout_ms", "perception.ocr_timeout_ms", 5000, Whole(500, 30000),
            "The longest one reading of the screen's pixels may take, in milliseconds. Compiling the helper the first "
            "time has a longer limit of its own."),
    Setting("ocr_overlay", "perception.ocr_overlay", False, Flag(),
            "Whether viewers outline the text read from the screen's pixels, and say what a box reads when it is "
            "pointed at. It is drawn over the screen: never in a screenshot or recording of the device."),
    Setting("settle_mode", "perception.settle", "perceptual", Choice(("perceptual", "exact")),
            "How a settle wait tells that the screen has stopped moving. `perceptual` compares a coarse grid of the "
            "screen's brightness, and stops watching small places that never stop moving -- a spinner, a pulsing "
            "dot, a caret; `exact` waits until not one byte of a screenshot changes, as SimMirror 1.0 did."),
    Setting("settle_tolerance", "perception.settle_tolerance", 2, Whole(0, 64),
            "How many cells of that grid, besides those found never to stop moving, may still change while the "
            "screen counts as settled."),
    Setting("settle_grid", "perception.settle_grid", 32, Whole(8, 64),
            "How many cells across that grid is: more sees smaller changes, fewer lets more motion pass. At 32, a "
            "cell of a phone's screen is about 12 points square."),
    Setting("build_tools", "build.tools", False, Flag(),
            "Whether agents are offered `sim_build_run` and `sim_test`. They run xcodebuild, so a host also has to "
            "allow commands.",
            embedded_default=True,
            sensitive=True),
    Setting("build_configuration", "build.configuration", "Debug", ConfigurationName(),
            "The build configuration used when a call names none."),
    Setting("build_timeout_minutes", "build.timeout_minutes", 20, Whole(1, 60),
            "A build or test run longer than this is stopped."),
    Setting("build_test_diagnostics", "build.test_diagnostics", False, Flag(),
            "Whether a test run with a failure also collects the simulator's diagnostics into its result bundle, as "
            "Xcode does by default. That can keep the run going for up to ten more minutes before the agent hears "
            "which tests failed, which the result bundle already says."),
    Setting("server_host", "server.host", "127.0.0.1", LoopbackHost(),
            "The address the daemon listens on: only 127.0.0.1 in this version, where the command line reaches it.",
            effect="restart", reach="global", sensitive=True),
    Setting("server_port", "server.port", 7466, Whole(1024, 65535), "The port the daemon listens on.",
            effect="restart", reach="global", sensitive=True),
    Setting("allowed_origins", "security.allowed_origins", (), Origins(),
            "Web origins, besides the daemon's own, that may call its API and open screen sockets.",
            reach="global", sensitive=True),
    Setting("frame_ancestors", "security.frame_ancestors", (), Origins(),
            "Origins, besides the daemon's own, allowed to show the viewer in an iframe.",
            reach="global", sensitive=True),
)  # fmt: skip


@dataclass(frozen=True)
class Section:
    """A top-level table of config.toml, as the settings panel shows it: one tab."""

    id: str
    title: str
    doc: str


SECTIONS: tuple[Section, ...] = (
    Section("", "General", "Whether devices are brought up at all."),
    Section(
        "connectors",
        "Connectors",
        "What reaches a device: the native helper or idb for full control, simctl to show it, mcpbridge to read it "
        "with Xcode 27.",
    ),
    Section("device", "Device", "Which Xcode, device type and runtime, and how devices are shared and put away."),
    Section("stream", "Stream", "How the screen is sent to a viewer."),
    Section("agent", "Agents", "What agents are offered, and what a person watching them sees."),
    Section(
        "perception",
        "Screen reading",
        "How agents read a screen whose accessibility says nothing, and how they tell it has stopped moving.",
    ),
    Section("build", "Build", "Building and testing apps from an agent."),
    Section("server", "Server", "Where the daemon listens. A change takes effect when it restarts."),
    Section("security", "Security", "Which other web pages may call the daemon or show its viewer."),
)

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
