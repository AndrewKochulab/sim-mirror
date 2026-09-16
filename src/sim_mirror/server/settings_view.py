# SPDX-License-Identifier: Apache-2.0
"""A scope's settings as the viewer's settings panel shows them (`SettingsView`), built from the settings table.

Every setting's section, rule, default, when a change takes effect and whether it is sensitive come from
`config.schema`; its value and where that value comes from, from the scope's settings -- or, for a setting only the
whole daemon reads, from the daemon's own. A value something above config.toml sets is locked, with why, since
writing the file would change nothing a person could see.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sim_mirror.config import schema
from sim_mirror.config.model import SimConfig
from sim_mirror.config.provenance import SettingOrigin
from sim_mirror.host_copy import HostCopy
from sim_mirror.protocol import SettingEntry, SettingsAccess, SettingsView
from sim_mirror.scope import Scope
from sim_mirror.seams import SettingsEditor

#: Where a layer above config.toml is, as a person reads it.
_ABOVE = {"environment": "{detail} in the daemon's environment", "command_line": "the daemon's command line"}


def access_of(editor: SettingsEditor) -> SettingsAccess:
    if editor.may_write_sensitive:
        return "write_sensitive"
    return "write" if editor.may_write else "read"


def json_value(value: Any) -> Any:
    return list(value) if isinstance(value, tuple) else value


def locked_reason(origin: SettingOrigin, copy: HostCopy) -> str | None:
    if origin.writable:
        return None
    return copy.setting_locked(_ABOVE[origin.layer].format(detail=origin.detail))


def settings_view(
    editor: SettingsEditor,
    *,
    scoped: SimConfig,
    daemon: SimConfig,
    scoped_origins: Mapping[str, SettingOrigin],
    daemon_origins: Mapping[str, SettingOrigin],
    copy: HostCopy,
) -> SettingsView:
    """Every setting as `editor.scope` sees it; those only the whole daemon reads, as the daemon does."""
    scope: Scope = editor.scope
    entries: list[SettingEntry] = []
    for setting in schema.SETTINGS:
        whole = setting.reach == "global"
        config, origins = (daemon, daemon_origins) if whole else (scoped, scoped_origins)
        origin = origins[setting.key]
        entries.append(
            {
                "path": setting.path,
                "section": setting.section,
                "doc": setting.doc,
                "rule": setting.rule.spec(),  # type: ignore[typeddict-item]
                "value": json_value(getattr(config, setting.key)),
                "default": json_value(setting.default),
                "origin": {"layer": origin.layer, "detail": origin.detail},
                "effect": setting.effect,
                "reach": setting.reach,
                "sensitive": setting.sensitive,
                "locked": locked_reason(origin, copy),
                "command": copy.setting_command(setting.path, None if whole else scope.id),
            }
        )
    return {
        "scope": scope.id,
        "access": access_of(editor),
        "sections": [{"id": section.id, "title": section.title, "doc": section.doc} for section in schema.SECTIONS],
        "settings": entries,
    }
