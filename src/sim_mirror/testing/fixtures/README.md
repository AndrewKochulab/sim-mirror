
`ax-macos-webkit.json` and `ax-macos-chromium.json` were recorded from the repository's synthetic web sample in
WKWebView (Tauri 2) and Electron 45.2.1. They contain only the sample's window subtree, window-relative frames and
synthetic page text; no process IDs, paths, private apps or menu-bar contents. Secure values are redacted. Chromium's
application root initially exposed 12 nodes; after AXManualAccessibility activation and initialization, its window
walk exposed 337. Tests read these documents without starting a browser or helper.
