# Mac Mini continuation prompt — issues #138 and #137

Copy the following prompt into a new Codex session on the designated Mac Mini, opened in the SimMirror repository.
This file records the handoff state; it is not evidence that the epic has been implemented or verified.

---

Continue the SimMirror macOS epic #138 and whole-Mac issue #137:

- https://github.com/AndrewKochulab/sim-mirror/issues/138
- https://github.com/AndrewKochulab/sim-mirror/issues/137

Work on `feature/138-whole-mac` in a separate worktree. Fetch origin, inspect the branch/PR and record the exact
starting SHA before editing. Read AGENTS.md, CONTRIBUTING.md, docs/architecture.md, docs/macos.md,
docs/security.md and docs/contributing/whole-mac-plan.md. Read the issue bodies/comments, the PR body and all
commits on the branch; the handoff may have been updated after this file was written. Do not reset, clean or overwrite
uncommitted work. Do not run another session's daemon or helper, or change its config or permissions.

## User decisions that persist

1. Desktop control and live capture belong on this Mac Mini. They were not exercised on the MacBook Pro.
2. Finish implementation and verification, create/update the pull request, and give an accurate completion summary.
3. macOS XCUITest is allowed only in a dedicated test session, with its limitation explicit. Its events bypass the
   helper's per-event exclusions, focus checks and release logic. Preserve the refusal in ordinary app-scoped mode;
   choosing mac-this alone must not grant the test-session exception. A dedicated session needs explicit entry,
   exclusive ownership, a visible warning, tracked test processes, cancellation and cleanup verification.
4. Keep screen evidence local. Publish only reviewed synthetic findings. No local usernames/absolute paths,
   private bundle IDs, signing identities, account information or private screen contents in the PR or commits.
5. Use one agent unless the user separately authorizes delegation. Keep progress updates concise and frequent.

## Exact implementation state at handoff

The branch began at `47acbb9` (3.7.0). The current code already implements app-scoped macOS native/web/CEF paths.
The whole-Mac connector is NOT implemented. There is no functioning mac-full, mac-this, global-input helper,
whole-display capture, physical takeover detector or desktop emergency stop. Do not start by assuming these exist,
and do not describe this as a testing-only handoff. Implementation and the mandated platform spike remain.

This branch adds two prerequisite fixes:

- Device change restoration is journaled BEFORE mutation. Cancellation during mutation keeps the original value;
  a journal write failure prevents dispatch and a retry attempts persistence again.
- During the cursor lead, a host pause, viewer touch, session stop or replacement prevents the announced gesture.
  This is not physical-input detection or mid-gesture cancellation.

The source fixes are commits `5b4d828` and `b64d7d4`. Twelve new regression cases failed against the original
implementation and passed with the fixes. Full `make lint` and `make coverage` passed: 3,138 Python tests (12 skipped),
264 Python files at or above 98%, and 245 viewer tests with coverage gates passing. Consult the PR for subsequent
results. `docs/contributing/whole-mac-plan.md` maps every requirement to code and tests,
including gaps not visible from the closed status of issues #135 and #136. Treat its delivery order and acceptance
matrix as the working checklist, and update it with actual evidence as work progresses.

## First: isolate and establish readiness

- Inspect current git status, branch, HEAD, tools and repository instructions. Use uv for Python; install viewer
  dependencies with npm ci. Read any applicable SimMirror build/test, capture or simulator skill before using its
  tools, and obey its project-scoping rules.
- Inventory macOS/Xcode/architecture, connected displays and backing scales, logged-in desktop state and existing
  SimMirror processes. Record only sanitized versions and geometry in public output.
- Prepare synthetic sample apps and a temporary test folder. Do not point the run at the user's accounts, private
  app data, password manager, terminal windows, browser profile or personal documents.
- Build this worktree's helper through make. Keep a stable locally selected signing identity and fixed helper path
  across runs; never publish the identity. The person grants needed macOS permissions. Do not operate Privacy &
  Security or approve system permission grants automatically.
- Use separate SIM_MIRROR_STATE_DIR, SIM_MIRROR_RUN_DIR, SIM_MIRROR_LOG_DIR and SIM_MIRROR_CONFIG, a free server port,
  and connectors.native.helper_path pointing at the worktree helper. Document local values in an ignored local
  evidence file. Start MCP against the intended worktree/root. Verify its reported version before driving it.
- Coordinate with the person immediately before the first live pointer/keyboard action. State exactly which
  synthetic apps and displays will be used and establish a working independent stop method first. The previous
  authorization chooses this machine; it does not mean the person is idle at any arbitrary moment.

## Mandatory platform spike before enabling control

Implement a narrowly scoped, explicitly invoked development probe if necessary. Do not add global input to normal
app adapters, run it from pytest, silently request permissions, or enable the production connector as a shortcut.

1. Test tagged CGEvents for clicks, drags, scrolling, Unicode typing and modifier chords. Verify actual UI state,
   not merely successful API return. Inspect pointer position and held button/key state after every interruption.
2. Compare hardware-state event timing with physical input and tagged synthetic events. Determine whether reliable
   takeover needs an event tap/Input Monitoring. Do not guess from the availability of an idle-time API. If physical
   takeover cannot be reliably detected, keep input unavailable and explain the missing dependency.
3. Test ScreenCaptureKit exclusions while synthetic protected apps launch, relaunch, move, resize and overlap.
   Inspect frames at filter transitions. Excluding a window is not an opaque mask; underlying pixels may remain.
   No frame may escape to screenshots, OCR, H.264, viewer or recordings before applying the same privacy policy.
4. Measure focused and all-visible AX reads for ten apps including a deliberately unresponsive sample. Define total
   deadlines, per-app timeouts, node/depth limits and partial-result reporting. Emergency stop must remain responsive.
5. Test a menu-bar status item and the proposed shortcut from the actual helper executable/signature. Verify they
   work independently of Python and when AX is hung. Test input release, stop latching and explicit restart.

Record observations and failures in local evidence and sanitize a concise result into the plan/PR. Do not fill
unknown results with expectations. If the platform requires a revised design, implement and document it first.

## Implement the remaining feature

Follow the delivery order in docs/contributing/whole-mac-plan.md. In particular:

- Add exact target-aware routing, including recovery/reconcile: mac-app and mac-full are never fallback candidates
  for each other. Disabled mode must cause no helper launch, permission query, AX read or screen capture.
- Add all six settings with correct defaults and global scope; enabled, agents_choose and excluded_apps are
  sensitive and use terminal-code confirmation. Verify environment/scoped/web paths cannot weaken the gate.
- Use one cross-process desktop claim and a session lease; serialize input even from the same agent. Agent
  `steal: true` is not the person's approval. Stop must latch across automatic recovery and daemon restart.
- Implement display capture, coordinates, display selection and all-visible/focused trees. Invalidate refs on
  focus/window/display-generation changes. Keep secure input, exclusions and hard system-change refusals at
  dispatch boundaries. Protect intermediate points of drags, not just endpoints.
- Implement app/system verbs, safe clipboard transport, appearance restoration, viewer banner and helper indicator.
  Include per-event cancellation, takeover, secure-input transitions and held-input release on all termination paths.
- Implement the separately authorized dedicated UI-test session contract. Prove that normal app-scoped tests retain
  their refusal, and never claim that XCUITest is governed by the helper's per-event guards.
- Write fakes and regressions for negative paths before enabling real delivery. Keep external programs in their
  owner modules, generated artifacts sourced from schemas, and compatibility promises unchanged.

## Automated gates

Run the gates appropriate to changes, followed by the complete acceptance checks:

    make lint
    make coverage
    make helper-coverage
    make generate
    make viewer-bundle
    git diff --check

Verify generation is stable and check the viewer size budget. If the app SDK or samples change, follow AGENTS.md
for sdk-lint, sdk-coverage and sdk-release-check with the correct Xcode and iOS 26 destination, plus the applicable
macOS/web/CEF/Tauri gates. Never start real platform tools from Python tests. A test failure needs an explanation
and fix; do not weaken coverage, regenerate compatibility baselines, or hide unsupported behavior.

## Live acceptance matrix

Use synthetic content and record PASS / FAIL / UNTESTED with evidence for every row:

- Default off: doctor, device list, direct attach, remembered selection and recovery do not start whole-Mac work.
- Terminal-code gating: ordinary web requests and scope overrides cannot enable it or alter protected settings.
- App/full separation: refuse cross-mode target attachment and fallback after helper failure or permission loss.
- Sample launch; Save panel; Finder drag/drop; permitted in-app prompt. A person handles system privacy grants.
- Focus changes between snapshot and input, during lead, between events, during drag and while typing.
- Protected app capture/tree/point/key/menu/open/launch refusal; new process/window and overlay races.
- Secure input refusal, no text in logs/intent/errors, and no queued keyboard events after the state changes.
- Observe destructive words, switches, checkbox/radio, row swipes and Trash, with explicit confirmation; prohibited
  system changes remain refused even with confirm. Test keyboard shortcuts and URL/file dispatch paths too.
- Physical takeover during tap, drag, held button, scroll and chord; resume behavior follows the documented contract.
- Indicator stop, shortcut stop and CLI stop; held input released; no automatic restart; slow/hung Python and AX.
- Two agents, two scopes and two daemons; no simultaneous ownership or input interleaving, including UI tests.
- Appearance restored after normal stop, cancellation, helper failure and daemon termination. Journal failures
  prevent a mutation. Verify original values survive a second change and repeated restarts.
- Screenshot/record regions, 30/60/120 fps output where supported, truthful delivered-frame count, MP4 fast start,
  H.264 viewer streaming and excluded-frame handling on every consumer.
- Dedicated UI-test session: explicit warning/entry, exclusive claim, normal-mode refusal, process cancellation,
  cleanup after test failure, runner crash and emergency stop. Record its helper-guard limitation explicitly.
- Multiple displays, negative origins, mixed scales, hotplug, Spaces, full-screen, minimize/resume. Verify Catalyst
  and Designed-for-iPad with available synthetic apps. Missing hardware remains UNTESTED.

Measure cold/warm snapshot and dispatch p50/p95, first-frame time, longest frame gap, capture cadence, CPU/memory,
AX deadlines and stop-to-last-input latency. Report sample counts and machine/display configuration. Distinguish
model thinking time from SimMirror latency: this code cannot guarantee that an external model never pauses.
Use bounded queues and event-driven capture; do not remove safety checks to improve timings.

## Synchronize and finish

Keep work on the same feature branch (or a clearly linked successor if it was already merged). At handoff, write
an ignored local evidence index and commit a sanitized verification report recording starting/tested/final SHAs,
OS/Xcode versions, commands/results, performance measurements, matrix outcomes and remaining issues. Include exact
resume instructions if anything is blocked. Push commits and update the PR with current behavior and results.
Do not commit raw logs/screenshots or upload them without reviewing their contents and authorization.

Keep the PR draft and both issues open until their acceptance is met or the user explicitly approves a scope
change. Report clearly what is implemented, what passed automated checks, what passed live checks and what remains.
Never label a scaffold, fake-only success or this prompt as completion of whole-Mac support.
