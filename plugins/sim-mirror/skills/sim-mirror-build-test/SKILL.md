---
name: sim-mirror-build-test
description: Build, install, launch or run automated iOS and Android project tests through SimMirror. Use for sim_build_run, sim_test, worktree builds, build/test failures and source verification; manual UI walkthroughs use the simulator skill.
---

# Build the intended code

Use live tool schemas; prefixes vary. Preserve the requested device, checkout, configuration and app identity.
Build tools are optional: if absent, inspect setup and host policy. Shell builds are appropriate only when allowed;
never use them to bypass a refusal.

1. Resolve the working folder. Pass `folder` for worktrees or after shell `cd`; MCP roots may name another checkout.
   Use `sim_device info` if the destination is unclear. Choose schemes/projects actually listed by the project or
   diagnostic rather than guessing through repeated builds.
2. Start `sim_build_run` or `sim_test` once. Set `wait_s` below the client timeout, leaving room for setup. Poll
   the same tool with its returned `build_id`; do not resubmit the build. After a transport timeout, inspect the
   next diagnostic/recent job before starting anew: the job may already exist.
3. Read the entire result. Compilation, tests, installation and launch are separate outcomes. `build ok` followed
   by `not launched` is not a working app; successful MCP transport does not mean tests passed. Report timeout,
   cancellation, compile failure and flaky retries accurately.
   Diagnose and complete requested installation/launch when possible; report a remaining blocker explicitly.
4. Fix reported source errors and rerun the relevant check. Use the exact failed identifier with `only_testing`,
   then expand to the requested suite after the fix. Do not send gestures while tests hold the device busy.
5. Check source folder, branch, commit and dirty state. When available, `sim_app info` with `bundle_id` and
   `folder` compares a source-stamped app. Without a stamp, report provenance unverified; matching commits with
   dirty files do not prove identical source contents.

Read [build decisions](references/builds.md) for configuration, signing, destinations and Android. For an interactive
smoke check, use the installed simulator skill or snapshot/action guidance.

Report what built, where it ran, passed/failed/skipped/flaky tests, and installation/launch outcomes. Keep diagnostics
focused on relevant failures. Do not publish private logs or evidence without the person's request.
