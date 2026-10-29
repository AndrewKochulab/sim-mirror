---
name: sim-mirror-setup
description: Install or troubleshoot SimMirror's MCP connection, plugin, project scope, viewer or device readiness. Use when tools are missing, refused, disconnected or acting on the wrong project; ordinary app navigation uses the simulator skill.
---

# Connect and recover

Identify the failing layer before changing anything. Use the client's configured executable/version: a plugin
launched with `uvx` need not have a global `sim-mirror` command. Keep credentials out of argv and reports.

1. **Discovery:** inspect MCP registration and available tools. Claude's plugin server is `device`; other clients
   choose their prefixes. Reconnect/rediscover after updates; never invent missing tools. `sim-mirror tools --json`
   describes the local install, not proof this client's scope is enabled or connected.
2. **Context:** `sim_device info` identifies the device/version without booting. Check launch folder, `--scope`,
   `--root` and effective settings for a wrong project. Shell `cd` does not move existing MCP roots. Name `folder`
   explicitly when building another checkout.
3. **Readiness:** use `sim_device health` and its actual cause: disabled settings, view-only input, WebDriverAgent,
   cable, Android authorization or another driver. Missing build tools can mean `build.tools` is off; enable them
   only within requested setup and host policy.
4. **Diagnostics:** when MCP cannot help, run the same install's `doctor --json --no-tap`. This skips the Settings
   test tap but still probes tools and may compile the text reader. Read `checks[].status` and `fix`: exit 2 is a
   warning, 1 a failure. Use the real tap only when appropriate to the authorized test.
5. **Repair and verify:** apply the smallest relevant repair, then retry the diagnostic once. An identical failure
   needs new evidence or an external change. After transport timeout, inspect state before replaying a mutation:
   it may have run. Never bypass host refusals through shell control or broad permission changes.

Read [recovery decisions](references/recovery.md) for the specific cause, or [client setup](references/clients.md)
for installation/upgrades. Follow only the applicable branch.

Report the observed cause, repair and verification. When blocked, name the exact human action, such as unlocking
the phone. A connected MCP server alone does not prove the device works.
