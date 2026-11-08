# Debug CEF host adapter

Use this adapter when a macOS app's window is a Chromium Embedded Framework (CEF) browser and you want SimMirror to
drive it completely: read the page, tap, type, press keys and chords, scroll, hover, drag, hold, right-click and choose
menu items. Everything happens inside the app's own process. The adapter never moves the person's pointer, activates
the app or posts a global event, and it works while the window is in the background or behind other windows.

It is header-only C++20 (`include/sim_mirror_cef/`) and compiles to nothing unless the host defines
`SIM_MIRROR_CEF_BRIDGE=1`. Define it only in development builds; a shipped build must leave it unset.
[The synthetic sample](../../examples/cef-app-sdk/) is a complete host.

## What it uses

| Request | How the host performs it |
|---|---|
| Read the screen | sdk/web's `bridge.js`, fixed bytes evaluated in the main frame, answered through a process message |
| Tap, double and triple click, hover, drag, long press, right and middle click, each with modifier keys held | `CefBrowserHost::SendMouseMoveEvent` / `SendMouseClickEvent`, with the click count and `EVENTFLAG_*_DOWN` flags |
| Keys, chords, text, clear, submit | Chrome DevTools `Input.dispatchKeyEvent` / `Input.insertText` over `ExecuteDevToolsMethod`, with macOS editing commands (`cmd+a` selects all, `alt+delete` deletes a word, ...) |
| Scroll | DevTools `Input.dispatchMouseEvent` (`mouseWheel`) at the point, with any modifier keys held |
| Context menu | The host's `RunContextMenu`: the menu's items are answered, or a named item is chosen, and native menu tracking never starts |
| Menu | The app's own `NSApp.mainMenu`, by title path, chosen in process |

`ExecuteDevToolsMethod` is in-process: it needs no remote-debugging port, and nothing outside the app can reach it.
CEF's `SendKeyEvent` and `SendMouseWheelEvent` are not used: a windowed browser in a window that is not key drops them.

## Integrate

1. **Accessibility, in every build.** In the `NSApplication` subclass, handle `accessibilitySetValue:forAttribute:`
   for `AXManualAccessibility` and `AXEnhancedUserInterface`: when set to true, call
   `browser->GetHost()->SetAccessibilityState(STATE_ENABLED)`, and do it for browsers created later. Report
   `AXManualAccessibility` as settable. This is how VoiceOver and SimMirror's accessibility mode see web content; without
   it a windowed CEF browser exposes none. It needs no bridge and belongs in release builds too.
2. **Command line.** In `CefApp::OnBeforeCommandLineProcessing` for the browser process (an empty process type), call
   `sim_mirror_cef::KeepDrawingInBackground(command_line)`. A window behind others otherwise stops drawing: scrolls never
   finish and screenshots go stale. Most hosts also append `use-mock-keychain`, or Chromium asks for the login keychain.
3. **Renderer.** First thing in `CefRenderProcessHandler::OnProcessMessageReceived`:
   `if (sim_mirror_cef::HandleRendererMessage(browser, frame, source, message)) return true;`
4. **Browser.** Keep one `sim_mirror_cef::Host`. Once the browser's window exists, on the UI thread:

   ```objc
   #include "sim_mirror_cef/mac.h"  // from an Objective-C++ file
   host.Start(sim_mirror_cef::MacHostOptions("com.example.App", "App", [] { return CurrentBrowser(); }), &error);
   ```

   Route `CefClient::OnProcessMessageReceived` through `host.OnProcessMessageReceived(...)` and
   `CefContextMenuHandler::RunContextMenu` through `host.RunContextMenu(...)` first; each returns true when the
   message or menu was SimMirror's. Call `host.Stop()` before the browser closes.

`Start` registers the browser's current native window, listens on 127.0.0.1 only with a new secret, and writes the
app's 0600 listing; `Stop` removes the listing if it is still this launch's. A sandboxed host sets `options.home` to
its container home. Replace `options.menu` to refuse or narrow which menu items may be chosen. If the app's menu
actions bring its window forward (as many do for a person choosing from the menu bar), give the bridge a quiet path:
find the item with `sim_mirror_cef::FindMenuItem` and run the same command without activating, or an automated menu
choice takes the person's focus.

## Limits

- Cross-origin frames are not read or targeted; the bridge reads the main frame's document.
- Chromium applies a wheel that continues an earlier scroll about a second after accepting it, so a scroll answers
  once it has landed (up to ~2 seconds).
- A held click or drag answers after its own duration.
- Native dialogs and panels, input-method UI and anything out of process stay with the person.
- Observe mode and destructive-action confirmation are enforced by SimMirror before a command reaches the app.
- A timed-out command may already have run: inspect before retrying.

## Check

`make sdk-cef-test` runs the core's tests (loopback admission, listing, chords, input plans) with a 98% line gate and
checks the embedded `bridge.js` is current (`scripts/cef_bridge_js.py`). `make sdk-cef-release-check` proves a host
built without the macro holds no server, listing or script. `make sdk-cef-sample CEF_ROOT=...` builds the sample.
