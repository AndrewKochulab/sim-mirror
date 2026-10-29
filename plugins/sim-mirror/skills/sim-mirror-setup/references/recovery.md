# Recovery decisions

| Evidence | Next step |
|---|---|
| No tools | Check registration, executable/uv, startup error and server name. Failed discovery differs from tools disabled by policy. Reconnect after repair. |
| `agent.tools`/`enabled` off | Explain the scope setting. Change it only within requested setup; host policy still applies. |
| Daemon unreachable | The standard launcher recovers refused connections. A host relay may need its host restarted or `serve --detach`; follow its message. Never replay uncertain actions. |
| Daemon deliberately stopped | Resume only when the current task authorizes using it again; the next call/reconnection may start it. |
| Wrong daemon/version/port | Compare client configuration with `sim_device info`. Never send credentials to an unverified listener or disable authentication checks. |
| Wrong project/worktree | Check root/scope; pass build `folder`. Run `doctor` in the worktree for missing settings. Copy settings only when requested, preserving overrides. |
| View-only simulator | Follow the connector reason. Inspect `helper status` for availability/version; build/install a dependency only within authorized setup. |
| Stale/covered ref | Snapshot again or scroll into view. Do not use `allow_stale` or coordinates to bypass a refusal. |
| Two failed reads | Check health once. For slow trees, use pixel text/screenshots meanwhile. Restart only for a diagnosed session problem. |
| Locked/disconnected/untrusted phone | Ask for the exact unlock, cable, trust or Developer Mode step. Network discovery does not prove USB readiness. |
| Repeating Camera prompt | Stop reconnect loops; check the helper path/version. Updates/rebuilds can need new consent; unchanged builds should reuse it. Never reset system-wide permissions. |
| Apple device-support update alert | Stop reconnecting. `Not Now` leaves the update pending. Let the person resolve Apple's prompt; do not install system updates or suppress the updater automatically. |
| WebDriverAgent | Read `wda status` and health's `last_error`; use Set up touch. Preserve project signing; report human permission/signing steps. |
| Android image/authorization/input | Follow doctor/device notes: install the requested image, authorize debugging, or set up scrcpy. Never reset adb or erase the AVD. |
| Observe refusal/other driver/person paused | Respect the boundary. Exact authorization permits `confirm`; explicit takeover permits `steal`. Wait for the person to resume. |
| Build running/device busy | Poll its `build_id`; avoid duplicate builds and UI gestures during tests. |
| Xcode hierarchy/session refused | Follow the named approval/session repair; never terminate another agent's Xcode session. |
| Wrong Xcode | Use project `device.developer_dir`; avoid changing global `xcode-select`. |
| Blank viewer/embed | Check session/sign-in, then origin/frame restriction. Never widen origins indiscriminately or reuse spent login codes. |
| Recording pending/failed | Follow `status`/`list` and the render error/raw-file location. Do not copy future files or delete recoverable input. |

For unresolved problems, report version, operation, relevant redacted diagnostics and verification. Publish an issue
or logs only when requested; exclude tokens, login codes and private app data.
