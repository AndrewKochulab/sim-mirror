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
    #: How a person gives an agent a folder to build in.
    build_folder_hint: str = "start `sim-mirror mcp` in the project's folder, or give it `--root /path/to/the/project`"
    #: The command that has Xcode approve SimMirror to use its tools, before a project's path.
    xcode_approve_command: str = "sim-mirror xcode approve"
    #: How a person points SimMirror at a native helper that is somewhere else.
    helper_path_hint: str = "set its path with `sim-mirror config set connectors.native.helper_path /path/to/it`"
    #: The command that builds the native helper for this install.
    helper_build_command: str = "sim-mirror helper build"
    #: Where a person reads how an app shares its view hierarchy through SimMirror's debug SDK.
    app_sdk_docs: str = "https://github.com/AndrewKochulab/sim-mirror/blob/main/docs/app-sdk.md"

    def off(self) -> str:
        return f"The iOS Simulator is off for this {self.scope_noun} ({self.settings})."

    def agent_tools_off(self) -> str:
        return f"The iOS Simulator's agent tools are off for this {self.scope_noun} ({self.settings})."

    def build_tools_off(self) -> str:
        return f"The iOS Simulator's build tools are off for this {self.scope_noun} ({self.settings})."

    def shells_not_allowed(self) -> str:
        return f"A build runs commands, and this {self.scope_noun} does not allow them ({self.settings})."

    def no_build_folder(self) -> str:
        return f"There is no folder to build in for this {self.scope_noun}: {self.build_folder_hint}."

    def companion_missing(self, configured: str) -> str:
        install = f"Install it with `brew install facebook/fb/idb-companion`, or {self.companion_path_hint}."
        if configured:
            return f"The simulator companion at {configured} cannot be run. {install}"
        return f"The simulator companion (idb_companion) is not installed. {install}"

    def helper_missing(self, configured: str) -> str:
        """Why the native connector cannot be used: its helper is not where it was looked for."""
        if configured:
            return (
                f"The native helper at {configured} cannot be run. Point connectors.native.helper_path at a built "
                f"helper, or empty it to use {self.owner_name}'s own ({self.settings})."
            )
        return (
            f"{self.owner_name}'s native helper is not built for this install. Build it with "
            f"`{self.helper_build_command}` (it needs Xcode), or {self.helper_path_hint}."
        )

    def helper_mismatch(self, binary: str, found: str, wanted: str) -> str:
        """Why a native helper that runs is not used: it was built for another version of SimMirror."""
        return (
            f"The native helper at {binary} is {found}, and this {self.owner_name} needs {wanted}. Build it again with "
            f"`{self.helper_build_command}`."
        )

    def helper_input_unreachable(self, reasons: str) -> str:
        """Why a native helper that started cannot drive the device."""
        return f"The native helper cannot send input to this simulator: {reasons}"

    def mcpbridge_missing(self, developer_dir: str) -> str:
        where = f" at {developer_dir}" if developer_dir else ""
        return (
            f"Reading the screen through Xcode needs Xcode 27 or later; the Xcode in use{where} is older, or has no "
            f"mcpbridge ({self.simulator_settings})."
        )

    def xcode_device_in_use(self, session: str) -> str:
        return (
            f"Xcode's tools already have a session on this simulator ({session!r}), and it can have one at a time: "
            "end that session where it was started, then read the screen again."
        )

    def xcode_not_approved(self) -> str:
        return (
            f"Xcode has not approved {self.owner_name} to use its tools yet. Xcode approves an agent that opens a "
            f"project through them: run `{self.xcode_approve_command} /path/to/App.xcodeproj` once, and allow it if "
            "Xcode asks."
        )

    def app_hierarchy_unread(self, app: str, reason: str) -> str:
        return f"{app} shares its view hierarchy, but this snapshot could not read it: {reason}"

    def app_hierarchy_cut(self, app: str, limit: int) -> str:
        return (
            f"The view hierarchy {app} shares was cut short: at most {limit} views are read "
            f"(connectors.app.max_nodes, {self.simulator_settings})"
        )

    def app_sdk_newer(self, app: str, protocol: int) -> str:
        return (
            f"{app} was built with a newer SimMirror SDK (app SDK protocol {protocol}) than {self.owner_name} reads: "
            "update it to read all it shares"
        )

    def app_hierarchy_none(self) -> str:
        return (
            "no app on it shares its view hierarchy. That is optional: a debug build that calls SimMirror.start() "
            f"does, so screens without accessibility labels still read well ({self.app_sdk_docs})"
        )

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

    def settings_this_scope_only(self) -> str:
        return f"This credential changes settings for its own {self.scope_noun}s only, one at a time."

    def device_in_use_elsewhere(self) -> str:
        return (
            f"That simulator is in use by a {self.scope_noun} of another host on this daemon: choose another, or let "
            f"{self.owner_name} make one."
        )

    def settings_code_wrong(self) -> str:
        return f"That code does not confirm this change: run `{self.confirm_command}` again for this one."
