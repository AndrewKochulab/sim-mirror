# Media

Pictures the README and the docs show. Every clone carries every picture ever committed, so `scripts/check_media_sizes.py`
keeps them small: a GIF at most 3 MB, a still image at most 400 KB, the folder at most 25 MB. Videos are attached to a
GitHub Release instead.

Captured from real runs, never mocked up. All of these were taken on 2026-09-16 from one machine -- macOS 26.6.2,
Xcode 26.6, an iPhone 17 Pro simulator on iOS 26.5 -- with SimMirror 0.1.0:

| File | Shows | How it was made |
|---|---|---|
| `hero.gif` | A Claude Code session tapping through Settings, its cursor drawn before each tap | Frames of the viewer while a real Claude Code session drove the device over MCP -- which is why the cursor's chip reads `claude-code` and the device says it is in use |
| `cursor.gif` | The agent cursor gliding to a row and the screen following | The same run, the moment around one tap |
| `viewer.png` | The viewer in a browser tab | A screenshot of the tab, live |
| `snapshot-vs-screenshot.png` | A snapshot beside a screenshot of the same screen | The real answers of `sim_screenshot` and `sim_snapshot`, taken a moment apart, set side by side with their measured token estimates |
| `iframe.png` | The viewer framed in another page | The `examples/iframe` page on its own origin, with a one-shot embed ticket |
| `view-only.png` | The view-only mirror without idb_companion | The same viewer with `connectors.idb.companion_path` pointed at nothing, so the simctl connector serves it |
| `doctor.png` | `sim-mirror doctor` on a Mac | The real output of `sim-mirror doctor --no-tap`, typeset for the docs rather than photographed from a terminal |
| `social-preview.png` | GitHub's social preview, 1280×640 | The project's name and claims beside a frame of `hero.gif` |
