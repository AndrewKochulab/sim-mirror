---
description: Open this project's live iOS Simulator viewer in the browser
allowed-tools: Bash(uvx --from sim-mirror==1.2.0 sim-mirror open:*)
---

Open SimMirror's viewer for this project, so the person can watch the simulator and use it.

Run this in the project's folder:

```
uvx --from sim-mirror==1.2.0 sim-mirror open
```

It starts SimMirror's local server when it is not running, and opens the viewer in the default browser with a
one-time sign-in code. Say that the viewer is open. If the command fails, say what it printed, and suggest running
`uvx --from sim-mirror==1.2.0 sim-mirror doctor`.
