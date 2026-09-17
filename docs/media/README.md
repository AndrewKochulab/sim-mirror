# Media

Pictures the README and the docs show. Every clone carries every picture ever committed, so `scripts/check_media_sizes.py`
keeps them small: a GIF at most 3 MB, a still image at most 400 KB, the folder at most 25 MB. Videos are attached to a
GitHub Release instead.

Captured from real runs, never mocked up. Most were taken on 2026-09-16 from one machine -- macOS 26.6.2, Xcode 26.6,
an iPhone 17 Pro simulator on iOS 26.5 -- with SimMirror 0.1.0; a row says so when its picture came from a later run:

| File | Shows | How it was made |
|---|---|---|
| `hero.gif` | A Claude Code session reading and writing calendar events, its cursor drawn before each tap | A screen recording made later, on 2026-09-17 with SimMirror 1.2.0, Xcode 27.0 and an iPhone 17 simulator on iOS 27.0, of a real Claude Code session driving a calendar app over MCP -- which is why the cursor's chip reads `claude-code` and the device says it is in use. Sped up four times and cut to 700 px wide to stay under the GIF limit; [the whole recording](https://github.com/AndrewKochulab/sim-mirror/releases/download/v1.2.0/sim-mirror-plugin-demo.mp4) is attached to the 1.2.0 release |
| `cursor.gif` | The agent cursor gliding to a row and the screen following | The 2026-09-16 Settings run, the moment around one tap |
| `viewer.png` | The viewer in a browser tab | A screenshot of the tab, live |
| `snapshot-vs-screenshot.png` | A snapshot beside a screenshot of the same screen | The real answers of `sim_screenshot` and `sim_snapshot`, taken a moment apart, set side by side with their measured token estimates |
| `iframe.png` | The viewer framed in another page | The `examples/iframe` page on its own origin, with a one-shot embed ticket |
| `view-only.png` | The view-only mirror without idb_companion | The same viewer with `connectors.idb.companion_path` pointed at nothing, so the simctl connector serves it |
| `doctor.png` | `sim-mirror doctor` on a Mac | The real output of `sim-mirror doctor --no-tap`, typeset for the docs rather than photographed from a terminal |
| `social-preview.png` | GitHub's social preview, 1280×640 | The project's name and claims beside a frame of the Settings run `hero.gif` showed until 1.2.0 |
| `settings-panel.png` | The settings panel's Server tab | A screenshot of the panel in Chrome, taken later on 2026-09-16 with Xcode 27.0 and an iOS 27.0 simulator, from a daemon started with `SIM_MIRROR_SERVER_PORT=7491` -- which is why the port shows as locked |
