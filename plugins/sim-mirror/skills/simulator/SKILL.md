---
name: simulator
description: Drive the iOS Simulator, a real iPhone, or an Android emulator or phone, with SimMirror's sim_* tools -- read what is on screen, tap, type, launch apps, check how something looks, record a demo. Use when a task needs the app running in the simulator or on a phone, or the person asks to try, test, look at or record something in it.
---

# Driving the iOS Simulator

The `sim_*` tools reach this project's own simulator. A person can watch everything you do: `/sim-mirror:open` opens
the live viewer, where each of your gestures shows as a cursor just before it lands.

The tools are named `mcp__plugin_sim-mirror_device__sim_*` -- `mcp__plugin_sim-mirror_device__sim_act`, say. The
server is `device` from 2.3 on and stays so; a session that still has 2.2's `mcp__plugin_sim-mirror_sim-mirror__*`
names searches its tools again. `sim_device info` ends with the version of SimMirror answering. Every tool is listed
whatever the device can do now: a call it cannot serve is refused with the cause and the fix, so read the refusal
rather than looking for the tool.

## The loop

1. **`sim_snapshot`** -- the screen as short lines, one per button, field, text or heading, each with a ref (`e4`) and
   where a tap lands. `mode: "diff"` (the default) gives only what changed since you last looked; `"full"` gives it
   all. Start here, not with a screenshot: it costs a small fraction of one. `max_elements: 10` lists only the first
   ten lines, when that is all you need.
2. **`sim_act`** -- every step you already know, in one call (up to 20): `{"tap": "e4"}`,
   `{"type": "Milk", "into": "e7", "submit": true}`, `{"swipe": {"from": "e2", "direction": "up"}}`,
   `{"press": "home"}`, `{"pause": 2000}` (up to 30000). Add a `wait` -- `{"for": "Saved"}`, `{"gone": "Loading"}` or
   `{"settle_ms": 400}` -- so the diff it answers with shows the screen after the change, not during it.
3. Read that diff and act again. Take a `full` snapshot only when you have lost track of the screen.
4. **`sim_screenshot`** only for what text cannot tell you: colour, layout, an image, an animation (`frames`). Keep
   `width` small -- 400 is plenty -- or give a `region` (a ref) to see one part. For an evidence file, give
   `save_to` (`"evidence/login.png"`, in your folder or the recording folder) and `return_image: false` when you do
   not need to see it -- never a separate capture command.

Waits look for text in a label or value, ignoring case: `"Locked"` also finds "Unlocked". When that matters, give
`{"for": {"exact": "Locked"}}` (the whole label, with its case), `{"regex": "^Lock(ed|ing)$"}` or
`{"text": "Locked", "case": true}`; `gone` takes the same. A regex is matched against a label's first 500 characters,
and one that could take minutes -- nested repeats like `(a+)+` -- is refused before anything plays: keep it simple and
anchored. `{"stable": {"for": X, "ms": 3000}}` waits until X has stayed
on screen that long -- a lock that has stopped moving, not one passing through. `timeout_ms` goes up to 120000; to wait
longer than a pause, wait for what you expect rather than pausing. A wait over 60 s needs a client that lets a tool call
run that long. Each `ok` line says `+ms` since the call began, and a met wait says when the read that saw it ran after
the last step -- this Mac's clock, so allow for a read's time in a timing check. A wait not met, or a failed step, makes
the answer an error: read it before going on.

Refs belong to the screen they were read from. After the screen changes a lot, snapshot again rather than reuse old
ones. After a pause, a snapshot more than 2 s old, or another agent's call, a touch reads the screen again first and
refuses a ref that moved or changed (`e5 changed since …`): call `sim_snapshot` and use the ref as it is now --
`allow_stale: true` only when you know the screen has not changed. A line ending `covered by tab bar` (or toolbar, navigation bar, status bar, bar, keyboard) is under that bar, and a touch on it
is refused: swipe the content -- from a point on it, not on the bar -- until the line no longer says so, then use its
ref.

Where the person has turned on observe mode -- a walkthrough on an account that is not yours to change -- a tap or
typing on what reads like Delete, Save, Send or Sign out is refused with "looks like it changes something" -- and one on
a button its app styles destructive, whatever it says, with "is a button its app styles destructive" -- and so are a
sideways swipe on a list row, a drop onto the trash and `submit` where such a button is. Scroll by swiping up or
down. Do not work around a refusal with a point tap, a drag or another path to the same control: tell the person what
you would do, and pass `confirm: true` with that one `sim_act` call only once they have asked for exactly that.

A snapshot that says its lines were read from the screen's pixels -- a game, a canvas, an app whose accessibility says
nothing -- lists text rather than controls: tap the text a control shows, and take its spelling with care. A settle
wait lets go of a spinner that never stops and says so; wait `for` the text you expect when it matters.

## The other tools

- `sim_device` -- `info` (which device, its state; does not start it), `list` (the simulators and connected devices
  you could use) and `choose` one by `udid`, `boot`, `restart` when apps stop answering snapshots (as they can after UI
  tests), `stop` when asked to stop the simulator or SimMirror (`shutdown: true` shuts a simulator down too, never a
  real device; never kill SimMirror's or idb_companion's processes yourself), `appearance` light or dark,
  `status_bar` (a `demo` one, or `clear`), `location` (a place, or `waypoints` to move along) and `clear_location`,
  `text_size`, `contrast`, `reduce_motion`. A real device gets back what these changed when it is let go. `rotate`
  turns a simulator `to` `portrait`, `landscape_left`, `landscape_right` or `upside_down` -- the way the app should
  lie -- and says how the app then lies: one that runs only upright stays portrait. It stays turned: turn it back to
  `portrait` when you are done. An Android device turns too, and is put back when it is let go.
- `sim_record` -- record the screen as an MP4, a GIF or both, each touch drawn where it landed: `start`, do the steps,
  `stop`, which answers each file's path and size -- also after the recording stopped by itself at its limit.
- `sim_app` -- `launch` a bundle id (`relaunch: true` starts it fresh), `terminate`, `install` a built `.app`,
  `open_url`, `logs` (an app's own lines, by its `bundle_id`, on a simulator and on a cabled phone).
- `sim_build_run`, `sim_test` -- present only where build tools are switched on. A long build answers with a
  `build_id`; call again with it to keep waiting. A failing test is named the way `only_testing` takes it, so run it
  again alone after a fix; `destination` runs the tests on another simulator, such as an older iOS. Working in a git
  worktree of the project, pass its path as `folder`, so the worktree's code is what gets built. The answer's `source`
  line names the folder, branch and commit built: check it is the code you mean before you report a result. Before
  recording or reporting what an app does, `sim_app` `info` with its `bundle_id` says which commit the installed app
  was built from -- where the person turned on `build.stamp_source` -- and whether that is the code in your folder now.

## Recording a demo

1. Get the screen to where the demo begins, then `sim_record` `start` -- `preset: "pr"` for an MP4 a pull request plays
   inline (450 wide and small), `format: "gif"` for something to show inline anywhere, `"both"` for a GIF and a
   full-quality MP4; `speed: "2"` makes a slow flow shorter and smaller; `also: "pr"` keeps the small MP4 for the pull
   request beside a full-size recording. A simulator shows a 9:41 demo status bar while it records. A recording stops
   by itself after `recording.max_seconds` (300 s unless the person changed it): for a long test, give
   `max_seconds` (up to 3600). `label: "unlock flow"` puts that in the file name; where files are kept is the
   person's setting, so copy a file elsewhere only after `stop` has answered with it.
2. Do the steps with `sim_act`, waiting for each screen to settle so the recording shows it. A screen that stands still
   is recorded still: the file runs as long as the recording did.
3. `sim_record` `stop`. It answers where each file is, and how big. A long recording may still be rendering after a
   minute: `stop` then says where it will be kept, and `sim_record` `status` or `list` says when it is. Never copy a
   file before then -- until it is finished it does not have its name.

To put it in a pull request or an issue: GitHub shows an image or a video inline only once it is uploaded into a
comment box, which `gh` cannot do. Where a browser tool is available, open the pull request, and upload the file into
its description or a comment with the browser's file upload; otherwise give the person the path and ask them to drag it
in. A GIF of 3 MB or less may instead be committed to the repository, beside the docs that show it, and linked.

## A real iPhone or iPad

When the person asks for their phone, `sim_device list` shows it among the connected devices and `sim_device choose`
switches to it; the person watching follows to it. A refusal that names `real_devices.agents_choose` means the person
picks it themselves, in the viewer. `sim_device info` says it is a real device and how it is connected. Its screen,
apps, logs, look and place work as on a simulator, and `sim_build_run` builds for it with the project's own signing.

Touching, typing and reading its element tree need WebDriverAgent, which the person sets up once: they press **Set up
touch** in the viewer -- and it reaches the device only over its cable. Until it drives the device, `sim_snapshot`
reads the screen from its pixels and `sim_act` is refused saying why; both answers, and `sim_device info`, end with
the device's `note:`. Before a run of steps on a real device, call `sim_device health`: it checks the screen, reading
and touch on the device now, one `key: value` line each -- with `text_reader:`, `note:` and `last_error:` -- and says
`ready to drive` or what stands in the way. When it is not ready, tell the
person what it says and wait for them: a cable that came out is plugged back in (straight into the Mac, the device
unlocked), and the device is attached again by itself within seconds -- `sim_device restart` does it at once. Do not
retry a refused call in a loop. Nothing types a passcode: ask the person
to unlock the device.

A long screen -- a feed, a table of hundreds of rows -- can take WebDriverAgent longer to read than a snapshot waits.
The snapshot then reads the screen's pixels and says the element tree is still being built on the device; the next
snapshots do too, at once, until it is done, and then read the tree again. Work from the text lines meanwhile, or
`sim_screenshot`; do not call `sim_snapshot` over and over to wait it out. A note that WebDriverAgent read only the
first items of a list means the rows past them are not listed, even when they are on screen: read them from a
screenshot, or ask the person to raise `real_devices.wda.source_max_children`.

One agent drives a real device at a time. While another agent is acting on it, or has in the last 2 minutes, your
calls that change it -- `sim_act`, `sim_app` launch, terminate, install or `open_url`, `sim_build_run`, `sim_test`, and
`sim_device` booting, restarting, stopping, turning or setting it -- are refused with `held by <agent> since <time>`. Tell the person
rather than taking it: pass `steal: true` only when they ask you to. Looking -- `sim_snapshot`, `sim_screenshot`,
`sim_device health` -- is never refused. On a simulator the answer only notes `<agent> also drove this device`.

## An Android emulator or phone

`sim_device list` shows Android emulators -- each an AVD on this Mac -- and connected Android phones beside the
simulators; `sim_device choose` one by `udid`, and `sim_device boot` starts an emulator headless. The same loop works:
`sim_snapshot`, `sim_act`, `sim_screenshot`. Android has its own buttons: `{"press": "back"}`, `"home"`,
`"app_switch"`, `"lock"`, `"volume_up"` and `"volume_down"`; a press the device cannot make is refused with the ones it
can. `sim_app` takes the app's package name as `bundle_id`, and `install` takes an `.apk` (or a folder of an app's split
APKs). `sim_build_run` and `sim_test` build a Gradle project with its wrapper, install it and run its instrumented
tests: `scheme` names the app module, `configuration` the variant (`debug`), and `only_testing` takes
`package.Class` or `package.Class/method`.

Without scrcpy the device is reached through adb alone: the screen updates a few times a second and typing takes ASCII
only. The device's note says so; the person can press **Set up touch** in the viewer to fetch scrcpy and see it live.
An Android phone is picked by the person unless `android.agents_choose` lets you; it is never shut down, and nothing
types its PIN: ask the person to unlock it.

## When something is wrong

- A tool answers that the simulator is off, or that its connector cannot do something (a view-only mirror cannot
  touch the screen, and reads it only from its pixels): tell the person what it said, and suggest `sim-mirror doctor`,
  which names the fix.
- Two snapshots in a row fail: stop, call `sim_device health`, and tell the person what stands in the way rather
  than calling again. On a real device that is almost always its cable.
- The tools act on the simulated device only, never on the Mac itself.
