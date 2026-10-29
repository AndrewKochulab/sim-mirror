# Build decisions

| Case | Decision |
|---|---|
| Configuration | Unset uses configured/scheme defaults. Debug/Staging can install different bundle ids; respect `build.expected_bundle_id`, never clear it to force installation. |
| Multiple projects | Choose the actual app workspace/project/scheme. A framework/test bundle may build without an installable app. |
| Worktree | `folder` must be allowed. Check returned source; fallback to the main checkout does not verify changed code. |
| Missing tools | Enable `build.tools` only within requested setup and host policy. Otherwise an authorized shell build can use `sim_app` to install; report unavailable capabilities. |
| Physical iOS | Use existing signing/team. Never re-sign for a simulator or claim phone source stamps. Ask for necessary unlock/signing decisions. |
| Tests | Use `only_testing`, `skip_testing`, `test_plan` intentionally. Compile failure means no tests ran; a passing retry remains flaky. |
| Destination | `sim_test destination` selects a compatible available device; respect other scopes' claims. `sim_build_run` installs on the current device. |
| Android | `scheme` is the module, `configuration` the variant; test ids are `package.Class` or `package.Class/method`. Instrumented tests need a running target; JVM tests use project commands. |
| Timeouts | Poll `build_id` with bounded waits. Client cancellation/timeout does not guarantee the build stopped. |
| After UI tests | Check health on read failures. A diagnosed simulator accessibility failure may need restart; recheck the app afterward. |
| Evidence | Verify installed app/state before recording. Preserve user settings/device; clean up only task-created resources. |
