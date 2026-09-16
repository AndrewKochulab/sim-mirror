# SPDX-License-Identifier: Apache-2.0
"""The words SimMirror uses for where its settings are and what its scopes are, which a host supplies.

A message such as "the iOS Simulator is off" has to say where to turn it on, and that place is the host's: the
`sim-mirror config` command for a standalone install, a settings page for an application that embeds SimMirror. So
every such message is built here from a `HostCopy`, whose defaults are the standalone install's, and a host passes
its own.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HostCopy:
    #: What a scope is called in messages: "project" standalone.
    scope_noun: str = "project"
    #: Where settings are changed, said in parentheses after a refusal.
    settings: str = "`sim-mirror config`"
    #: Where the simulator's own settings are, when that is more specific.
    simulator_settings: str = "`sim-mirror config`"
    #: Why a scope has no simulator when the host itself has turned the area off.
    area_off: str = "The iOS Simulator is not available here."
    #: How a person points SimMirror at an idb_companion that is installed somewhere else.
    companion_path_hint: str = "set its path with `sim-mirror config set connectors.idb.companion_path /path/to/it`"
    #: How the process that owns SimMirror is named when another one is using a device.
    owner_name: str = "SimMirror"
    #: Said after a reason a connector cannot be used.
    doctor_hint: str = "Run `sim-mirror doctor` to see why."
    #: The command that changes one setting at the terminal, before its path and value.
    set_command: str = "sim-mirror config set"
    #: The command that opens the settings panel able to change settings.
    open_settings_command: str = "sim-mirror open --settings"
    #: The command that shows a sensitive change waiting to be confirmed, and its code.
    confirm_command: str = "sim-mirror settings confirm"

    def off(self) -> str:
        return f"The iOS Simulator is off for this {self.scope_noun} ({self.settings})."

    def agent_tools_off(self) -> str:
        return f"The iOS Simulator's agent tools are off for this {self.scope_noun} ({self.settings})."

    def build_tools_off(self) -> str:
        return f"The iOS Simulator's build tools are off for this {self.scope_noun} ({self.settings})."

    def shells_not_allowed(self) -> str:
        return f"A build runs commands, and this {self.scope_noun} does not allow them ({self.settings})."

    def companion_missing(self, configured: str) -> str:
        install = f"Install it with `brew install facebook/fb/idb-companion`, or {self.companion_path_hint}."
        if configured:
            return f"The simulator companion at {configured} cannot be run. {install}"
        return f"The simulator companion (idb_companion) is not installed. {install}"

    def too_many_booted(self, running: int, limit: int) -> str:
        count = "1 simulator is" if running == 1 else f"{running} simulators are"
        return (
            f"{count} already running and in use, and this Mac keeps at most {limit} booted "
            f"({self.simulator_settings})."
        )

    def no_runtime(self, wanted: str) -> str:
        if wanted:
            return f"No {wanted} simulator runtime is installed ({self.simulator_settings})."
        return "No iOS simulator runtime is installed. Add one in Xcode → Settings → Components."

    def claimed(self, owner: str, pid: int) -> str:
        return (
            f"Another {owner} on this Mac (pid {pid}) is already showing this simulator. "
            f"Stop it there, or give this {self.owner_name} a different device."
        )

    def setting_command(self, path: str, scope_id: str | None) -> str:
        """How a person changes one setting at the terminal: for a scope, or -- None -- for every scope."""
        scoped = f" --scope {scope_id}" if scope_id is not None else ""
        return f"{self.set_command} {path} <value>{scoped}"

    def setting_locked(self, where: str) -> str:
        """Why a setting cannot be changed from a page: something above config.toml sets it."""
        return f"Set by {where}, which config.toml cannot override: change it there."

    def settings_read_only(self) -> str:
        return (
            f"This page can read the {self.scope_noun}'s settings but not change them: "
            f"open them with `{self.open_settings_command}`."
        )

    def settings_need_terminal(self, paths: str) -> str:
        """Why a change a page cannot confirm is refused outright."""
        return f"{paths} can only be changed at the terminal ({self.settings})."

    def settings_confirm(self, paths: str) -> str:
        return (
            f"Changing {paths} needs a person at the terminal: run `{self.confirm_command}` "
            "and enter the code it shows for this change."
        )

    def settings_code_wrong(self) -> str:
        return f"That code does not confirm this change: run `{self.confirm_command}` again for this one."
