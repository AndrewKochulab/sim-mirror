# Whole-Mac implementation and acceptance plan

Tracking: [epic #138](https://github.com/AndrewKochulab/sim-mirror/issues/138),
[whole-Mac #137](https://github.com/AndrewKochulab/sim-mirror/issues/137).
Audit baseline: `47acbb9` (3.7.0). This is an implementation plan, not a statement that whole-Mac control ships.
Live desktop tests belong on the designated Mac Mini, not on the development MacBook Pro.

The user approved UI tests only in a dedicated test session, with the limitation explicit: XCUITest sends input
outside the helper, so helper exclusions, focus checks and per-event cancellation cannot govern those events.
This does not authorize removing the normal app-scoped test refusal. The dedicated session must own the Mac,
announce the limitation before starting, track and cancel its test runner, and require explicit entry.

## Branch progress

- `5b4d828`: persist restoration before dispatch; failed persistence prevents mutation and can be retried.
- `b64d7d4`: refuse input after a pause, viewer touch, stop or reconnect during the cursor lead.
- Twelve regression cases fail on the baseline and pass with these changes. Full `make lint` and `make coverage`
  pass: 3,138 Python tests, 12 skips; 264 Python files meet the 98% gate; 245 viewer tests and viewer coverage pass.
- The remaining delivery steps below are not implemented or live verified. See [the continuation prompt](../../PROMPT.md).

## What exists

The closed foundation and app-scoped issues (#135 and #136) delivered the `mac` device kind, macOS and Catalyst
build destinations, unhosted unit testing, window capture, desktop action schemas, native and web SDKs, per-process
accessibility, screenshots, recording, viewer support, permission diagnostics and app-scoped overlays. Later work
added CEF input, capture scheduling and signing continuity. Preserve these paths and the 3.x compatibility promise.

`core/runtime.py` composes device backends, connector selection, agent actions, builds, ownership and recordings.
`core/mac_backend.py` and `connectors/mac/connector.py` assume every Mac target is one application.
`helper/Sources/HelperPlatform/MacWindowDevice.swift` captures one owned window; `MacAccessibility.swift` checks PID
ownership throughout. Neither supplies display capture or global input. No `mac-full` connector, `mac-this` target,
whole-Mac settings, global emergency stop or physical-input detector exists at the audit baseline.

Closed issues do not prove every live acceptance case. Their recorded results explicitly leave Catalyst and
Designed-for-iPad apps, mixed-scale displays, other Spaces, full-screen transitions, minimize/resume and permission
renewal behavior partly or wholly unverified. Record each result separately; do not infer them from ordinary windows.

## Integration gaps

| Requirement | Existing seam | Necessary change and regression evidence |
|---|---|---|
| Explicit target routing | `connectors/registry.py`, `core/availability.py`, `core/manager.py` | Selection currently uses kind alone and keeps all usable connectors as fallback candidates. Route by target identity; app and whole-Mac connectors must never be fallbacks for one another, including recovery and reconcile. |
| Device identity and lifecycle | `platform/identifiers.py`, `core/mac_backend.py` | Add exact `mac-this` identity; retain `macapp-*`. Never power off or reboot the host. Separate app launch/release bookkeeping from desktop session lifetime. |
| Off without side effects | Connector probe, backend choices, doctor | Disabled whole-Mac probe/list/attach must not enumerate windows, read AX, request permissions or start a helper. Test with dependencies that fail if called. |
| Sensitive global configuration | `config/schema.py`, model, config writer and confirmation routes | Enabled, agents_choose and excluded_apps are all sensitive; the issue's phrase “two sensitive ones” conflicts with its table, which lists three. All six proposed settings are global. Verify file, environment, scoped and web update paths. |
| Ownership | `core/drivers.py`, `storage/claims.py` | Default ownership exempts local devices, expires after 120 seconds, permits agent `steal`, and does not serialize calls by the same caller. Whole-Mac needs an exclusive session lease and serialized input; takeover requires a person's approval. Use a shared cross-daemon claim. |
| Emergency stop | Helper main loop, `core/manager.py` recovery, CLI stop | A stopped helper must not automatically restart. Latch the stop, cancel batches/builds, release held input and require an explicit person-driven restart. Indicator and shortcut must work independently of Python and AX traversal. |
| Person takeover | `core/actions.py` InputPause | Existing pause checks occur between gestures and see viewer input or host signals. Physical takeover must interrupt a drag/held key immediately in the helper. Never infer physical-input detection from viewer-only tests. |
| Protected apps | Native capture, AX, hit testing and event delivery | One policy must exclude capture, trees, point input, keyboard focus, menus and app operations. Cover newly launched/relaunched apps, overlays, unknown ownership, remote views and filter refresh failures. |
| Secure input | Native event delivery and screen metadata | Check immediately before keyboard dispatch; clear queued text and release held keys on transition. Warn without including password values or typed text in logs, errors or intent labels. |
| Focus race | Snapshot/action contract and helper dispatch | Bind planned input to PID, window and a generation. Recheck at dispatch and throughout gestures. An app activation must not silently retarget a previously planned key event. |
| Display geometry | ScreenGeometry, capture and action coordinates | Define top-left display-local points, global display origin, pixel scale and display identity. Negative origins, mixed scale, hotplug, rotation and display changes invalidate refs. `all` requires an explicit multi-display representation; do not pretend one display is all. |
| Tree modes | Helper AX reader, snapshot schema and max_elements | Focused-window default; explicit all-visible mode grouped by app. Apply exclusion before reading. Bound total time as well as nodes/depth; one hung app must not block capture or emergency stop. |
| Display privacy | ScreenCaptureKit, FrameHub, OCR and recording | Exclusion must precede encoding and fanout, including cached frames, OCR and screenshots. A filter transition must not expose a stale frame. Excluding a window can reveal underlying content; it is not equivalent to an opaque mask. |
| App/system verbs | `platform/macos.py`, tools and DeviceControl | Deliberate launch/activate/quit/open, clipboard through stdin/stdout (never secret argv), appearance restoration. Reject excluded targets and prohibited system changes regardless of `confirm`. Define file/URL dispatch policy. |
| Restore after interruption | `core/device_changes.py` | Original values must be journaled before dispatch. At baseline most mutations were journaled only after success, losing restoration on cancellation/crash after a side effect. Test interruption inside the adapter and failure to write the journal. |
| UI tests | `build/xcodebuild.py` | Current macOS check rejects hosted/UI tests. XCUITest input bypasses helper enforcement; retain that refusal outside the user-approved dedicated test session. Test explicit entry, exclusive ownership, warning, cancellation and process cleanup. |
| Visible control | Viewer status and helper status item | Persistent whole-Mac banner and click-to-stop indicator; optional agent cursor must not hide the control indicator. Exclude SimMirror's own windows from agent pictures. |
| Capture reuse | ScreenFeed, H264Stream, FrameHub, recordings | Keep event-driven frame delivery and bounded queues. Regions, delivered-frame metrics and encoding cadence must remain truthful. |
| Static confinement | `scripts/check_mac_confinement.py` | Preserve app-scoped restrictions. If global adapters get a dedicated location, check dependency boundaries too; renaming a file must not silently bypass the guard. |

## Delivery order

1. Establish baseline lint/coverage; add interruption-safe restoration with regression tests.
2. Specify and test a pure session state machine: disabled, ready, active, person-paused, stopped. Stop is latched;
   no restore/reconnect/timer implicitly grants control again. Define focus generations, exclusion decisions and
   release of held input. Make all clocks and platform facts injectable.
3. Add settings and identity-aware routing, doctor reporting, cross-process ownership and terminal-confirmed setup.
   Add denied-path tests first. Keep the connector unavailable until its required helper capabilities are present.
4. Implement the helper's separate desktop mode with display capture, exclusion updates and bounded AX reads.
   Add control indicator and emergency stop before enabling event delivery. Keep global adapters outside app SDKs.
5. Add guarded semantic actions and CGEvent delivery, focus checks, takeover and secure-input handling. Test
   cancellation at each dispatch boundary and during long gestures. Shared queues must not delay stop handling.
6. Wire app/system actions, clipboard, restore, tree modes, display selection and viewer status. Maintain schema
   sources and regenerate outputs; keep older helper versions explicitly unavailable for whole-Mac mode.
7. Add fake desktop integration coverage across MCP, CLI, viewer input, settings reload, recovery and crash paths.
   Validate the public compatibility surface, distribution, containment, documentation and bundle budget.
8. Run the Mac Mini spike before calling platform-dependent behavior verified. Fix measured failures, rerun relevant
   gates and then run the complete recorded acceptance flow. Keep the PR draft until required evidence exists.

## Required live spike and performance evidence

Apple documents hardware-state event timing and application-based ScreenCaptureKit filters, but those APIs alone
are not evidence that synthetic input can be distinguished reliably or that changing filters cannot leak a frame.
References: [event source state](https://developer.apple.com/documentation/coregraphics/cgeventsourcestateid),
[event timing](https://developer.apple.com/documentation/coregraphics/cgeventsource/secondssincelasteventtype(_:eventtype:)),
[capture filters](https://developer.apple.com/documentation/screencapturekit/sccontentfilter).

On the Mini, record OS/Xcode versions, architecture, display geometry, helper signature and permission state. Use
synthetic applications and an isolated daemon/config/state/run/log directory with its own port. Never reuse another
session's helper or daemon. Keep raw paths, bundle IDs, signing identities and screen evidence private.

- Compare hardware input with tagged synthetic events for click, drag, wheel and chords; check whether Input
  Monitoring is required. An idle-time counter's availability is not proof that it can distinguish the two.
- Test the status item and shortcut from the plain helper binary, with the target app hung and Python unresponsive.
  Measure stop-to-last-event latency and prove key/button release. Test permission loss and event-tap disablement.
- Test capture with protected apps already open, newly launched, moving/resizing, relaunching and overlapping.
  Inspect every frame around filter updates; repeat for screenshots, stream, recording and OCR.
- Measure focused AX and all-visible AX with ten apps, including an unresponsive one. Report cold/warm p50/p95,
  total deadline, nodes visited, partial-result reasons and effect on capture. Node caps alone do not bound time.
- Measure first frame, warm snapshot, input dispatch, settle, longest frame gap, CPU and memory. Distinguish helper
  processing latency from agent/model think time. Keep safety checks off slow subprocess paths and use bounded queues.
- Exercise Retina/non-Retina displays, negative origins, hotplug, Spaces, full-screen and minimize/resume. Mark absent
  hardware as untested; do not substitute a fake for live acceptance.

The final walkthrough covers sample launch, Save panel, Finder drag, permitted prompt, protected-app refusal,
observe confirmation, physical takeover, each emergency stop path and restoration after daemon termination.
Permission grants/privacy settings must be performed by the person: “answer a permission prompt” must not become
an exception to the hard-coded prohibition on modifying privacy settings.

## Completion rule

Do not close #137 or #138 on a scaffold, fake-only success, or a handoff document. Every acceptance row needs either
implementation plus tests and required live evidence, or an explicit approved scope change. The eventual PR must
separate implemented, automatically verified, live verified, refused by design, and still unverified behavior.
