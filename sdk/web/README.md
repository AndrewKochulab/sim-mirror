# Debug webview bridge

Use this adapter only when a macOS app's Accessibility tree cannot read or act on its web content. The host owns the
integration: it registers a native window and its own webview, starts `sim_mirror_web::Server`, and executes requests
on that webview's UI thread. SimMirror never injects into an arbitrary installed app.

The complete Tauri host hook is in [the sample](../../examples/tauri-app-sdk/src/mirror.rs). A CEF host uses
[the Debug CEF host adapter](../cef/README.md) instead: it serves the same protocol from C++, reads the page with this
script and acts with CEF's trusted in-process input. Deno may be the app's
backend while its window host is Rust or C++; integrate with the window host, not an unrelated backend process.
The listing PID must be the process owning the native window. Do not run this server in a separate sidecar.

The callback receives `Request::Hierarchy { window_id, max_nodes }` or `Request::Input(command)`. Evaluate `script()`
once in the registered page, then call `globalThis.__SimMirrorWeb.capture(options)` or `.input(command, offset)`.
The Tauri sample shows the exact JSON envelope. Supply the native window size in points and the webview's offset
from its top-left corner; never infer coordinates from the desktop pointer. Restrict navigation to the app's own
origins. Cross-origin frames are not traversed.

The adapter reads DOM IDs, labels and bounds, redacts passwords, invokes DOM click/pointer handlers, sets the native
input value and dispatches input/change events, and scrolls DOM containers. Canvas code must handle those synthetic
events. Browser defaults that require trusted input, native menus/dialogs and unsupported chords need an explicit
host hook or an actionable refusal. Observe mode remains enforced by SimMirror before dispatch.

The server listens on 127.0.0.1 only, with a new secret per launch and a private mode-0600 app-SDK listing. It accepts
only registered native window IDs, bounded requests, the exact loopback Host and an Authorization header, without
Origin. The secret stays in the host; the page receives neither credentials nor a socket endpoint. Stop the server
with the host. A timed-out operation may already have started, so inspect state before retrying.

Both the server and script are behind `cfg(debug_assertions)`; Release exports only no-op stubs. Hosts must also
exclude the integration module from Release, as the sample does. Do not copy `bridge.js` into a release frontend's
assets. `make sdk-web-release-check` checks Debug markers are present and Release markers absent;
`make sdk-web-test` checks the loopback boundary and DOM behavior. These checks complement the host's own release
bundle inspection.
