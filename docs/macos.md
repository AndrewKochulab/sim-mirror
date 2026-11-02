# macOS apps

A macOS target is one app process and one selected window. SimMirror captures that window through ScreenCaptureKit
and delivers input through the app's Debug SDK or its accessibility actions. It never moves the person's pointer,
activates the app, or posts global keyboard or mouse events. Whole-Mac control (#137) is postponed.

## Setup

Use macOS 14 or later with a logged-in desktop session. Open the target app once, then use `sim_device list` and
`sim_device choose` with its `macapp-…` identifier. The identifier follows the bundle ID across launches; the selected
bundle path is kept so another installed copy does not silently replace it. `sim_device health` checks the selected
app's capture, hierarchy and input capabilities.

The native helper needs **Screen Recording** in System Settings → Privacy & Security. **Accessibility** is needed
for apps without the SDK. Grant these to the helper binary, whose path `sim-mirror helper status` reports, rather
than to Python. `sim-mirror doctor` reads the helper's last nonprompting permission report; it does not request a grant.
A report from an earlier helper run is evidence of that run, not a fresh permission check.

For development, keep the helper's signing identity stable across rebuilds:

```sh
security find-identity -v -p codesigning
make helper-build HELPER_SIGN_IDENTITY="Apple Development: YOUR IDENTITY"
```

The helper has a fixed signing identifier. Ad-hoc signing remains the default, including `make helper-release`;
replacing an ad-hoc binary can invalidate its permissions. Stop this checkout's helper sessions before rebuilding.
Do not stop helpers owned by another daemon. macOS may periodically ask again for Screen Recording access. The
cadence on macOS 26 has not been established by these checks; do not promise a fixed renewal interval.

## Three ways to reach an app

| Mode | Use it for | Input and hierarchy |
|---|---|---|
| A: SimMirrorKit | Your native AppKit or SwiftUI Debug app | Own views and public accessibility selectors, optional SwiftUI debug data; in-process control, text and menu actions |
| B: per-process Accessibility | Apps without the SDK, including Catalyst, Designed for iPad and Tauri | One `AXUIElementCreateApplication(pid)` root, with PID checks on every element; only actions the app exposes |
| C: Debug web bridge | Webviews with missing AX actions, controlled-input or canvas gaps | A host hook registers its own webview and native window; a small in-page adapter reads DOM nodes and dispatches page events |

Mode A is compiled out of Release, previews and extensions. Link the `SimMirrorKit` Swift package product and call
`SimMirror.start()` early in the app. See [the SDK guide](app-sdk.md#macos) and [the native sample](../examples/mac-app-sdk/README.md).
SwiftUI accessibility nodes need not declare `NSAccessibilityProtocol`; the walker reads their public selectors.
Optional SwiftUI debug data is limited to macOS 26. Secure-field values are never included in a hierarchy.

A sandboxed Debug app needs `com.apple.security.network.server` to listen on loopback. Its listing belongs under its
container's `Library/Application Support/SimMirror/apps`; an unsandboxed app uses the same path under its home.
The daemon checks only the selected bundle's normal and container paths. Listings must be owned by the current user,
mode 0600, regular files without symlinks, and match the selected live PID. Each launch uses a new secret, sent in
an Authorization header. No secret belongs in a URL, argv, source file or log.

### Tauri and WKWebView

Tauri's macOS window embeds WKWebView. Mode B reads its `AXWebArea`, DOM identifiers (`AXDOMIdentifier`) and native
AX roles, including links, buttons, text fields, headings, checkboxes, radio buttons, pop-up buttons, lists and groups.
A newly opened webview can initialize its AX tree lazily; read again before treating a first empty tree as permanent.
The helper checks a readable AXValue after setting it; a page that ignores the write gets an explicit refusal.
Read the expected result after other actions too.

`AXPress`, settable text and exposed scroll actions need no app changes. The helper scrolls an actionable element into
view when it exposes `AXScrollToVisible`. Canvas and custom controls may expose neither meaningful nodes nor actions.
Use the [Debug web bridge](../sdk/web/README.md) for those gaps. The [Tauri sample](../examples/tauri-app-sdk/README.md)
shows the host hook; apps whose AX support already works do not need it.

### Deno desktop hosts, Chromium and Electron

Deno does not identify the window engine. Inspect the built app, not its framework name:

```sh
otool -L /path/to/Sample.app/Contents/MacOS/Sample
ls /path/to/Sample.app/Contents/Frameworks
```

Follow linked host libraries when the executable delegates to one. WKWebView uses the Tauri path above. For Chromium
or Electron, the helper tries `AXManualAccessibility` and `AXEnhancedUserInterface` on the selected application root.
It restores only attributes it changed from a readable previous value, and never disables one already enabled.
Electron's convention is not a guarantee for CEF: a windowed CEF browser exposes no web subtree until its host turns
renderer accessibility on. A CEF 149 desktop host inspected for this work exposed native UI only.

For CEF hosts, the [Debug CEF host adapter](../sdk/cef/README.md) covers both gaps. Its accessibility hook (step 1,
for every build) lets VoiceOver and Mode B read the page. Its Debug bridge serves Mode C with trusted in-process input:
CEF mouse events, and DevTools input over `ExecuteDevToolsMethod`, with no remote-debugging port. Keys, chords, text,
scrolls, hover, drags, long presses, context-menu items and menu paths then work while the window is in the
background. The host integrates it explicitly; SimMirror does not inject into an installed process or alter its binary.

## Windows, capture and coordinates

`sim_device windows` lists this process's windows. Select one with `sim_device window` and `window: ID` or a unique
title, then take a fresh snapshot. A sheet or popover that has its own native window must be selected separately.
A window stream does not composite child windows or another app's overlays. App menus are invoked by title path,
without opening menu tracking on screen.

All action points and snapshot frames start at the selected window's top left, in points; screenshots and recording
regions use the existing screen coordinate rules. Capture preserves Retina scale; moving between two 2× displays was verified with a synthetic animated window.
Moving a window refreshes its origin. After resizing, select the window again to reconnect capture and refresh refs. Restore a minimized or hidden
window before connecting; a minimized stream can stop producing new frames. Other Spaces, full-screen transitions
and mixed-scale displays require verification on the particular Mac and are not implied by a successful normal-window test. A new app process or an ended session
forgets the previous window ID; window IDs are not reused across launches.

`sim_screenshot` supports regions and `save_to`. `sim_record` uses the existing H.264 pipeline and 30, 60 or 120 fps
output, region capture and fast-start MP4s. The capture line reports actual delivered frames; a 60 fps file does not
prove that the app rendered 60 distinct frames per second. The viewer uses the same selected window.

## Actions and limits

| Request | Behavior |
|---|---|
| Tap | SDK control action or local view event; Mode B needs an AX action |
| Type | SDK text insertion; AX selected text or settable value. `clear: true` replaces; otherwise text is inserted or appended |
| Scroll / swipe | SDK scroll container or DOM scrolling; Mode B exposed page actions or scrollbar increments |
| Keys and chords | In-process SDK or CEF adapter (macOS editing commands included); Mode B refuses rather than posting keys to a background process |
| Hover | SDK view or DOM event; Mode B refuses |
| Right click | DOM context-menu event where handled; the CEF adapter answers a native context menu's items, or chooses `item`, without menu tracking; elsewhere native menu tracking is refused when it could take focus |
| Menu | A leaf item in the target app's own menu, by title path; a bridge whose listing does not offer `menu` leaves it to Mode B |
| Drag / held click | In-process mouse events through an adapter whose listing offers `gestures` (the CEF adapter); refused by the others, with no global fallback |
| Open URL | Refused because LaunchServices can select another process; navigate through the app instead |
| Appearance | Refused where the adapter does not provide a per-app override |
| Rotate, hardware buttons, status bar, location | Refused: these device operations do not apply to a Mac app |
| Text size, contrast, reduced motion, pasteboard | Refused because these would change shared Mac state |
| Open/Save panels, permission prompts, input-method UI | A person handles out-of-process UI; no foreign AX elements are read or acted on |

Observe mode and destructive-action confirmation apply to all three input modes. A bridge does not bypass them.
Timeouts leave execution uncertain if work had already started; inspect state before retrying. DOM events are synthetic:
pages that require trusted browser input or native dialogs must refuse or provide an explicit app-specific hook.

`sim_app install` selects a built `.app`; launch, terminate, info and logs stay bound to that bundle and process.
`sim_device restart` relaunches it. `sim_device stop` ends the session; `shutdown: true` also quits the selected process
when no other scope uses it. Automatic cleanup does not quit an app that was already running.

`sim_build_run` uses an Xcode macOS scheme; `catalyst: true` selects the Mac Catalyst destination. Rust, Deno and
Tauri build systems are not Xcode schemes: build their app with the project's own command, then choose the running
app. `sim_test` requires a provably unhosted, unit-test-only macOS scheme. UI tests are refused because XCUITest
drives the real pointer; hosted unit tests are refused because launching their GUI host can take focus. Existing signatures and sandbox entitlements on macOS build products are preserved; build reports keep
the source commit, but no provenance key is written into the signed app.

## Settings

`mac.apps.enabled`, `mac.apps.agents_choose` and `mac.apps.accessibility` default to true and are sensitive settings.
Turning off Accessibility still allows a trusted linked Debug SDK to provide input. `mac.apps.keys_to_process`
defaults to false; no verified process-posted key adapter is provided, so enabling it does not enable a fallback.
See the [configuration reference](reference/configuration.md) and [sensitive settings](settings.md#sensitive-settings).

Keep local app paths, bundle IDs, signing identities, screen contents and raw diagnostic evidence out of public
issues, PRs and fixtures. The repository's samples use synthetic data; recordings and screenshots of private apps
belong in a private temporary folder.
