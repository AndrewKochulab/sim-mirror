---
name: simulator
description: Drive the iOS Simulator with SimMirror's sim_* tools -- read what is on screen, tap, type, launch apps, check how something looks. Use when a task needs the app running in the simulator, or the person asks to try, test or look at something in it.
---

# Driving the iOS Simulator

The `sim_*` tools reach this project's own simulator. A person can watch everything you do: `/sim-mirror:open` opens
the live viewer, where each of your gestures shows as a cursor just before it lands.

## The loop

1. **`sim_snapshot`** -- the screen as short lines, one per button, field, text or heading, each with a ref (`e4`) and
   where a tap lands. `mode: "diff"` (the default) gives only what changed since you last looked; `"full"` gives it
   all. Start here, not with a screenshot: it costs a small fraction of one.
2. **`sim_act`** -- every step you already know, in one call (up to 20): `{"tap": "e4"}`,
   `{"type": "Milk", "into": "e7", "submit": true}`, `{"swipe": {"from": "e2", "direction": "up"}}`,
   `{"press": "home"}`. Add a `wait` -- `{"for": "Saved"}`, `{"gone": "Loading"}` or `{"settle_ms": 400}` -- so the diff
   it answers with shows the screen after the change, not during it.
3. Read that diff and act again. Take a `full` snapshot only when you have lost track of the screen.
4. **`sim_screenshot`** only for what text cannot tell you: colour, layout, an image, an animation (`frames`). Keep
   `width` small -- 400 is plenty -- or give a `region` (a ref) to see one part.

Refs belong to the screen they were read from. After the screen changes a lot, snapshot again rather than reuse old
ones.

A snapshot that says its lines were read from the screen's pixels -- a game, a canvas, an app whose accessibility says
nothing -- lists text rather than controls: tap the text a control shows, and take its spelling with care. A settle
wait lets go of a spinner that never stops and says so; wait `for` the text you expect when it matters.

## The other tools

- `sim_device` -- `info` (which device, its state; does not start it), `boot`, `restart` when apps stop answering
  snapshots (as they can after UI tests), `appearance` light or dark.
- `sim_app` -- `launch` a bundle id (`relaunch: true` starts it fresh), `terminate`, `install` a built `.app`,
  `open_url`, `logs`.
- `sim_build_run`, `sim_test` -- present only where build tools are switched on. A long build answers with a
  `build_id`; call again with it to keep waiting. A failing test is named the way `only_testing` takes it, so run it
  again alone after a fix; `destination` runs the tests on another simulator, such as an older iOS.

## When something is wrong

- A tool answers that the simulator is off, or that its connector cannot do something (a view-only mirror cannot
  touch the screen, and reads it only from its pixels): tell the person what it said, and suggest `sim-mirror doctor`,
  which names the fix.
- The tools act on the simulated device only, never on the Mac itself.
