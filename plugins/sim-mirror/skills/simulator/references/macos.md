# macOS app targets

A `mac` target is one app process and one selected window. Use `sim_device list`, `choose`, `info` and `health`.
Never substitute whole-Mac automation, AppleScript, global events or real-pointer input when an action is refused.
Whole-Mac control is postponed.

Use `sim_device windows`, then `window` with a native window ID or unique title. Take a fresh snapshot after switching
or resizing. Sheets and popovers may have separate windows. Restore a minimized or hidden app window before connecting.

Native Debug apps can link SimMirrorKit. Apps without it use process-bound Accessibility. Tauri uses WKWebView;
Deno desktop hosts may use WebKit, Electron or CEF, so inspect the engine instead of assuming it. A Debug web bridge
requires an explicit host integration and is only needed where AX lacks useful nodes or actions. A CEF host with the
Debug CEF adapter (its session note says "trusted input through the app's debug bridge") also takes keys, scrolls,
hover, drags, long presses and context-menu items, in the background: a right click's line lists what its menu
offered, and `{"right_click": ref, "item": "Copy"}` chooses one. From SimMirror 3.1 it also takes
`{"tap": ref, "count": 2}`, `"button": "secondary" | "middle"`, and `"modifiers": ["command", "shift"]` on a tap,
long press, drag, right click, hover or scroll. Accessibility alone refuses these. An open modal dialog is read alone. Do not inject into or modify a user's app
without authorization.

Read state after acting: AXValue or a synthetic page event can be accepted without producing the intended change.
Type appends unless `clear: true`; secure values stay redacted. Use menu title paths rather than opening native menu
tracking. Keys, hover, scroll and custom controls vary by adapter; an unsupported operation must explain why.
After a timeout, inspect state before retrying.

Observe-mode refusals apply in every mode. Use `confirm: true` only for the exact action the person authorized.
System dialogs, foreign-process panels, global appearance/accessibility preferences and the shared clipboard are
outside this mode. Do not use coordinates or shell commands to bypass a refusal.

`sim_build_run` supports Xcode macOS schemes and `catalyst: true`. Build Rust/Tauri/Deno apps with their own project
commands, then connect. `sim_test` refuses macOS UI-test targets because XCUITest moves the real pointer; use an
unhosted unit-test scheme when focus must remain untouched.

Capture regions and recordings through `sim_screenshot` and `sim_record`. Check the recording's capture line as well
as its requested output rate. Keep private paths, app identifiers, signing identities and screen evidence out of public
issues and PRs. Local capture does not authorize publication.
