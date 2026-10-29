---
description: Open this project's live device viewer -- iOS Simulator, iPhone or Android -- in the browser
allowed-tools: Bash(uvx --from sim-mirror==2.5.0 sim-mirror open:*)
---

Open SimMirror's viewer for this project, so the person can watch the simulator and use it.

Run this in the project's folder:

```
uvx --from sim-mirror==2.5.0 sim-mirror open
```

It starts SimMirror's local server when it is not running, and opens the viewer in the default browser with a
one-time sign-in code. Report success only when the command succeeds. If it fails, report the relevant error and
use the setup skill, or `uvx --from sim-mirror==2.5.0 sim-mirror doctor --json --no-tap`, to diagnose it. Do not
publish the one-time sign-in code.
