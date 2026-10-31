# Tauri macOS sample

A small Tauri 2 application embeds a local page in WKWebView. It includes buttons, inputs, a password field,
a checkbox, a select, a link, a scrolling list and a canvas. It starts without taking the frontmost app's place.

```sh
make sdk-tauri-sample
open -g -n examples/tauri-app-sdk/target/MacWebSample.app
```

Mode B requires no bridge: choose the running app in SimMirror, then inspect its AX tree and the results of each action.

For Mode C, quit the sample and run:

```sh
open -g -n --env SIM_MIRROR_WEB_BRIDGE=1 examples/tauri-app-sdk/target/MacWebSample.app
```

 `src/mirror.rs` registers its own window,
executes the adapter only in its own webview, and serves the existing authenticated loopback protocol. The secret
never enters JavaScript. The registered webview fills the window's content area; the hook accounts for its title bar.
A different window layout must supply the actual webview offset.

The hook and script are excluded from Release with `cfg(debug_assertions)`. Run `make sdk-web-test
sdk-web-release-check sdk-tauri-release-check`; no hook is needed in an app whose Accessibility tree and actions already work.
