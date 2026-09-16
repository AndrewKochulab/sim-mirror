# SPDX-License-Identifier: Apache-2.0
"""The surface a host application embeds SimMirror through.

A host builds one `Runtime` from its seams -- where settings come from, where state lives, which device a scope
remembers, what a scope may do, who is asking, and the words its messages use -- mounts the router factories under its
own prefixes, and gives its agents the relay's command line. Everything a host needs is named here; anything else in
the package may change between minor versions.

Three of these are here because a real host needed them and had to reach past this surface to get them:
`InvalidScope`, which `Scope` raises; `JsonDeviceMemory`, so a host need not write a `DeviceMemory` of its own; and
`claims_dir`, which is how every host on one Mac sees the same device claims and so refuses each other's devices
rather than fighting over one.
"""

from __future__ import annotations

from sim_mirror.config.model import SimConfig
from sim_mirror.core.devices import JsonDeviceMemory
from sim_mirror.core.manager import SimulatorUnavailable
from sim_mirror.core.runtime import Runtime
from sim_mirror.host_copy import HostCopy
from sim_mirror.mcp.relay import relay_command
from sim_mirror.scope import InvalidScope, Scope
from sim_mirror.seams import (
    Admission,
    Authenticator,
    Caller,
    ConfigSource,
    DeviceMemory,
    HeldDevice,
    Person,
    Policy,
    Refused,
    StateStore,
    UsageProbe,
)
from sim_mirror.server.agent_routes import create_agent_router
from sim_mirror.server.http_routes import create_http_router
from sim_mirror.server.socket_routes import create_socket_router
from sim_mirror.storage.app_support import claims_dir

__all__ = [
    "Admission",
    "Authenticator",
    "Caller",
    "ConfigSource",
    "DeviceMemory",
    "HeldDevice",
    "HostCopy",
    "InvalidScope",
    "JsonDeviceMemory",
    "Person",
    "Policy",
    "Refused",
    "Runtime",
    "Scope",
    "SimConfig",
    "SimulatorUnavailable",
    "StateStore",
    "UsageProbe",
    "claims_dir",
    "create_agent_router",
    "create_http_router",
    "create_socket_router",
    "relay_command",
]
