# Agent workflows

SimMirror ships four short skills. Each loads only when its task applies; platform-specific details are bundled
references. The live MCP schemas and results remain authoritative.

| Skill | Request |
|---|---|
| `simulator` | Navigate, fill forms, inspect layout or walk through an app |
| `sim-mirror-setup` | Install, update or diagnose tools, scopes, viewer and device readiness |
| `sim-mirror-build-test` | Build the intended checkout, run tests, install/launch and verify source |
| `sim-mirror-capture` | Save screenshots or record completed local evidence |

## Install and update

The [Claude plugin](claude-code-cli.md) includes all four and `/sim-mirror:open`. Update the marketplace and plugin
through `/plugin`, then reconnect or start a fresh session. Automatic updates depend on the client's settings;
publishing a version does not replace the tools already loaded by a running session.

For [Codex](codex.md) or [Cursor](cursor.md), download `sim-mirror-skills-2.6.0.zip` from the
[2.6.0 release](https://github.com/AndrewKochulab/sim-mirror/releases/tag/v2.6.0), extract it into a temporary folder,
and copy its four skill directories into your app project's `.agents/skills/`. Include their `references/` folders.
Review existing same-named skills before replacing them. Keep one installed copy of each skill and configure
[MCP separately](other-mcp.md) at the same release. Updating the archive does not update MCP or vice versa.

The canonical source is [plugins/sim-mirror/skills](../../plugins/sim-mirror/skills/). You can also copy its four
directories from the matching release checkout. These are workflows for apps using SimMirror, so install them in
the app project rather than assuming this source repository is your app.

[Codex](https://learn.chatgpt.com/docs/customization/overview) and
[Cursor](https://cursor.com/help/customization/skills) document `.agents/skills` discovery. Restart/reload the client
if new skills are not visible. Local SimMirror still needs a Mac with access to the target device; syncing a skill
to a remote agent does not connect that agent to your Mac.

Verify both parts: the client lists the four skills and discovers `sim_*` tools; `sim_device info` identifies the
intended device, then a snapshot works. For phone interaction, check health before gestures. For hosts without
skills, the MCP server still provides the core interaction rules and tool descriptions.

## Results and recovery

`sim_act` preserves text and also returns `structuredContent` with `completed_steps`, `failed_step`,
`failed_step_may_have_run` and `wait_met`. Step numbers start at one. A failed connector operation may have partly
executed; the flag is deliberately conservative. A wait failure never undoes completed actions. Errors before
execution may have only text; a lost connection leaves execution uncertain.

Build/test results include `build_id`, `state`, `kind`, `installed`, `launched`, `folder`, `commit` and `dirty`.
`state` describes the build/test job; a compiled app can still fail installation or launch. Failed/cancelled/timed-out
jobs and unsuccessful build-run delivery set `isError`. Tests do not use the build-run installation/launch flags;
their runner manages the test host. Human-readable text retains test counts, failures, flaky retries and diagnostics.

An upstream discovery failure is a JSON-RPC error, rather than a successful empty tool list. A legitimately disabled
manifest remains empty. Changed schemas/descriptions trigger tool-list invalidation as changed names already did.

## Maintaining the skills

`make lint` checks each Markdown file's 500-word budget, tool names and bundled references. The release packages
those exact files; tests extract the archive and validate it again. Argument examples run through the actual action
and wait parsers. [Workflow validation](../contributing/skill-validation.md) maps decisions to regression tests and
independent agent scenarios. These checks reduce errors; they do not guarantee every model, app or device succeeds.
