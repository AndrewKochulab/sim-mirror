# Real devices

An iPhone or iPad connected to your Mac shows up in SimMirror beside the simulators: in the viewer's device picker, in
`sim-mirror devices`, and to an agent once you pick it. What SimMirror can do with it depends on what is there:

| | Needs | Gives |
|---|---|---|
| **Tier 1** | Xcode, and the device trusted and in Developer Mode | The device listed and picked; a screenshot about once a second; apps installed, launched and ended; its look and place changed; its log, over the cable |
| **The cable screen** | The device plugged in by cable, and the Camera permission for SimMirror's helper once | The live screen as H.264, like a simulator's |
| **Tier 2** | WebDriverAgent, set up once with your signing team (**Set up touch** in the viewer) | Touching, typing, buttons, and the element tree for `sim_snapshot` |

Without tier 2 the device is view-only: an agent has `sim_screenshot`, `sim_app`, `sim_device`, `sim_record` and a
`sim_snapshot` read from the screen's pixels. With WebDriverAgent on but not driving the device -- not set up yet, or
its cable out -- `sim_act` stays listed and is refused saying why, and `sim_device info` and each pixel-read snapshot
end with the device's `note:`; so a client that keeps the tools it was first given still has `sim_act` once touch is
back. With `real_devices.wda.enabled` off, `sim_act` is listed too, and refused saying it is off.

`sim_device health` checks the device now rather than what was set up: a screenshot taken, its screen read once,
and whether WebDriverAgent answers -- one `key: value` line each, after `verdict: ready to drive` or `not ready to
drive`, with the text reader's state, the device's note even when touch works, and the last error of what drives it.
An agent calls it before a run of steps on a real device, and a harness can use it as its preflight; `sim-mirror wda
status` and the doctor's `touch now` check ask WebDriverAgent whether it answers too, for every real device the
daemon drives.

## Picking one

A person picks a real device -- in the viewer's picker, where it is listed under **iPhones and iPads** with how it is
connected (USB or Wi-Fi) and what stands in its way, or with

```sh
sim-mirror devices choose <udid>
```

An agent can pick one too: `sim_device list` shows the simulators and connected devices it could use, and `sim_device
choose` switches to one -- a real device while `real_devices.agents_choose` is on, as it is unless a host that embeds
SimMirror turns it off. Whoever watches follows to the new device.

A device that is not connected is refused with why, never replaced by a simulator. Letting it go (the viewer's power
button, or choosing another device) leaves it running; a real device is never shut down. `real_devices.enabled` turns
the whole thing off -- a device being driven when it is turned off has its session ended there and then, its screens
told why -- and a host that embeds SimMirror has it off unless it turns it on.

SimMirror never powers a real device on, restarts it or shuts it down, and its tools say so. `sim_device boot` opens a
session on it: the screen, its log, and touch where WebDriverAgent is set up. `sim_device restart` starts that session
over -- the cable screen, WebDriverAgent and the log attached again, whoever watches kept watching, what was changed on
it left as it is -- and answers `reconnected`, saying the device itself was not restarted; one that cannot be reached
any more is refused with why. Restart the phone by hand when it needs it. On a simulator, `restart` still shuts it
down and boots it again.

## A person comes first

An agent waits for a person: before its gestures, for the person's hand to have been still for a moment, and refuses
with why after two seconds of it not being still. That hand is **the person's input through a viewer**. A finger on
the phone's own screen reaches no viewer, and nothing the phone tells the Mac says it is there, so SimMirror cannot
wait for it: an agent's tap can land while someone is holding the phone. Keep your hands off a phone an agent is
driving, or stop the agent first.

A host that embeds SimMirror and knows better -- its person pressed "I'm using the phone", say -- tells agents so by
passing an `InputPause` to `Runtime.build` ([embedding](embedding/python-fastapi.md#the-seams)); a host sharing the
daemon pauses a scope's device for so many seconds, at most ten minutes, with `PUT /api/v1/scopes/{scope}/pause`
(`DaemonHost.pause`), and it lifts by itself when they run out
([sharing the daemon](embedding/shared-daemon.md#a-persons-pause)). While it answers paused for a device, an agent's batch of steps waits up to two seconds for it to lift and then refuses, saying a
person paused agents; a batch already under way stops before its next gesture, the steps before it kept. The pause
holds back gestures and typing (`sim_act`) only: launching an app, a build, a recording or a change to how the device
looks are not touches, and go ahead. The tools stay offered while it lasts, and each refusal says why.

## Tier 1: devicectl

SimMirror reaches a device through Xcode's `devicectl`, with the Xcode in `real_devices.developer_dir` (else
`device.developer_dir`). Nothing is installed on the device for this.

- **The screen**: a screenshot about once a second when there is no cable screen (0.75 s each over Wi-Fi).
- **Apps** (`sim_app`, `sim_build_run`): installing a built app, launching, ending and opening URLs. A build for a real
  device keeps its project's own signing, and Xcode registers the device with the project's team; a project that names
  no team is signed by the [team found for it](#which-team-signs-it).
- **How it looks and where it is** (`sim_device`, the viewer's **Display** menu, `sim-mirror device`): light or dark,
  text size, increased contrast, reduce motion, and a simulated location or route. Each is offered only when the
  device says it can do it; the phones tested cannot simulate a status bar, which the cable screen shows as 9:41
  anyway.
- **The log** (`sim_app logs`): over the cable, read through the device's own lockdown service -- the Mac's pairing
  record, never a new pairing -- and kept while the device is driven, up to `real_devices.log_buffer_mb`. A cable
  that comes out and goes back in keeps what was read before. An app's lines are those of its process -- its
  executable, named by its installed bundle as devicectl lists it (`August` for `com.august.iossapp`) -- or of its
  bundle's last part, or any line that names its bundle id.

## The cable screen

With the device plugged in, SimMirror's native helper reads its screen the way QuickTime does and serves it as a
simulator's: screenshots, and H.264 at the viewer's frame rate. The first time, macOS asks whether
**sim-mirror-helper** may use the Camera -- that is how macOS names reading a device's screen -- and SimMirror uses
screenshots until you allow it. While the screen is read, the device's status bar shows 9:41; the capture runs only
while someone looks and for 30 seconds after, then the device's own status bar comes back.

Historical cable-screen measurements on a physical iOS device over USB (2026-09-29):

| | |
|---|---|
| First picture after plugging in | about 7.5 s (the cable switches mode), then about 1 s |
| A screenshot | 6 to 24 ms |
| First H.264 frame of a stream | 28 ms |
| Frames a second while scrolling, at 30 asked | about 28 |
| Frames a second while scrolling, at 60 asked (`stream.fps`) | about 55 |
| Agent-call figure reported in that run (tool unspecified) | about 60 ms |

The old agent-call figure is not a bound on snapshots or actions. See the
[2.4.0 performance report](performance.md) for named tools measured through MCP on a physical iOS device; streaming frame
rate and startup were not remeasured in that run.

`real_devices.screen` chooses: `auto` takes the cable screen when it can, else WebDriverAgent's screenshots when it
runs, else devicectl's; `usb` and `wda` use only that one and refuse the device without it, and `screenshot` always
shows devicectl's.

## Tier 2: WebDriverAgent

[WebDriverAgent](https://github.com/appium/WebDriverAgent) is Appium's open-source XCTest runner (BSD-3-Clause). It
drives the device the way Xcode's UI tests do. SimMirror never ships it: it fetches one pinned release, checks it, and
builds it on your Mac with your team.

### Setting it up, once

1. **Pick the device**, plugged in and unlocked. While it can only be watched, its bar says **Set up touch**.
2. **Press Set up touch**, then **Set up touch** in the note that opens. SimMirror fetches release 16.12.10 (commit
   `00c38220c3e84906c965b996ffc4c12d09fef62f`), refuses it unless its SHA-256 is
   `27343e6064b204f7a1bff20096597d5f318564465d91e2fee66ea71bc1ddc222`, makes [two changes](#safety), and builds it with
   `xcodebuild` as `dev.simmirror.<team>.WebDriverAgentRunner` for the device -- which lets Xcode register the device
   with your team, so the profile covers it. The first build takes a minute or two; the note follows it, and the
   device can be touched once it is done. From a terminal, `sim-mirror wda setup --device <udid>` does the same. Xcode
   must be signed in to your developer account.
3. **On the device, the first time**, trust your developer in Settings › General › VPN & Device Management if it asks,
   and turn on Settings › Developer › **Enable UI Automation**.

That is the only time you are asked. After that SimMirror builds it again by itself when it has to -- for a new Xcode,
or another device the team signs for -- and starts it whenever the device is picked. A build that fails is shown with
why, and tried again only when you press **Try again**.

`real_devices.wda.enabled` (on) turns WebDriverAgent off, leaving real devices view only; a host that embeds SimMirror
has it off unless it turns it on. Turned off while a device is driven, it is view only there and then: its session is
started over without touch -- the device itself left as it is -- its viewers come back to a screen they can only watch,
and an agent's `sim_act`, one under way included, is refused saying WebDriverAgent is off. With `real_devices.screen`
set to `wda`, the screen still comes from WebDriverAgent's screenshots, and nothing else does. The runner ends when the device is let go unless `real_devices.wda.keep_running` says
otherwise. A runner that ends by itself -- killed on the device or by `devicectl` -- is not started again until the
next tool call on the device, or a viewer opened on it, which waits for it to come up; other calls on the same device
wait for that start too rather than starting it again, and nothing on other devices waits for it. `sim-mirror wda
status` says what is set up; `sim-mirror wda uninstall <udid>` removes the runner from a
device (Settings › General › VPN & Device Management also lists it).

### Which team signs it

WebDriverAgent, and a build of your app for a real device, are signed by a development team, found in this order:

1. **Your project's own team**: the one its settings name (`DEVELOPMENT_TEAM`, in the project or its `.xcconfig`
   files), for the folder the agent works in. A build of your app always keeps its project's own signing; Xcode
   registers the device with that team and makes its profile, as it does when you run on a device from Xcode.
2. **`real_devices.team_id`**, for a device used outside a project, or a project that names no team. Set it for one
   project with `--scope`.
3. **The one team this Mac signs for**, when its development certificates sign for exactly one.

So several projects, each with its own team -- a company's and your own, say -- use one phone side by side: each builds
and runs WebDriverAgent under its own team and bundle id. Registering the device takes one of the team's device slots,
and a person with the right to register devices in that team (usually its Admin or Account Holder). `sim-mirror wda
teams` lists the teams this Mac signs for. Nothing of SimMirror's needs setting up in the developer portal: Xcode
makes the App ID and profile it needs.

A finger's stroke goes as one request once it lifts, replayed at the speed it was made so a drag stays a drag and a
flick a flick, with at most four of its points: its ends, the one before its end, and the one farthest off its line.
WebDriverAgent is told to act at once rather than wait for the app to go idle first, as it does by default. Historical
measurements on a physical iOS device, before the 2.4.0 action changes:

| | Before optimization | After optimization in that run |
|---|---|---|
| A tap | 1.85 to 2.5 s | 0.34 s |
| A half-second drag, done after the finger lifts | 7 to 11 s | 0.85 s |

What is left is XCTest performing the touch, and the stroke's own length. Text of any kind -- accents, emoji, other
scripts -- is typed whole, never through the pasteboard.

## Connectivity workflows

With WebDriverAgent set up and the iPhone connected by USB, a person in the viewer or an agent can open Control
Center with a swipe, use its Wi-Fi, Bluetooth and airplane mode controls, then return to the app. On an iPhone with
Face ID, swipe down from the top-right corner; on one with a Home button, swipe up from the bottom. These are ordinary
screen gestures through the viewer or `sim_act`, not dedicated radio-control commands.

For a BLE or Wi-Fi accessory, a useful walkthrough is:

1. Connect the app to the accessory and start a recording if you need evidence.
2. Open Control Center and disconnect Wi-Fi or Bluetooth, or toggle airplane mode.
3. Return to the app and check its disconnected state.
4. Re-enable the connection, return to the app and check whether it reconnects; stop the recording.

Keep the USB cable connected so SimMirror's control connection does not depend on the Wi-Fi connection being tested.
Use `sim_snapshot` to read each screen; where a control has no usable ref, use a screenshot and a coordinate gesture.

Control Center's Wi-Fi and Bluetooth buttons **disconnect connections rather than fully turn off the radios**;
some services and accessories remain available. To test with either radio fully disabled, use its switch in Settings.
Airplane mode can retain Wi-Fi or Bluetooth according to the phone's previous choices, so check the resulting state.
See [Apple's explanation of Control Center connectivity](https://support.apple.com/en-us/102412).
Restore the connection settings after the walkthrough: changes made by tapping the phone's UI are not covered by
SimMirror's automatic restoration of appearance and location settings.

## Safety

A real device is usually someone's own phone. SimMirror treats it that way:

- **What is installed**: nothing for tier 1 and the cable screen. With WebDriverAgent, one test-runner app you build
  yourself, sandboxed like any other. While it runs it sees and drives what is on the screen -- any app's, as UI tests
  do -- and it cannot read other apps' stored data, photos, messages or keychain.
- **Nothing leaves the Mac.** WebDriverAgent's API is told to listen only on the device's own loopback
  (`USE_IP=127.0.0.1`), which the Mac reaches through the cable by usbmuxd; nothing on the device's network can reach
  it. Its MJPEG screen stream listens on every interface in the upstream source, so SimMirror changes that one line to
  bind it where the API is. The change is made by exact text and refused when the file is not as SimMirror knows it; a
  checkout of your own in `real_devices.wda.path` must carry the same change, or it is refused. WebDriverAgent is
  reached only over the cable. The release's download is the only request SimMirror makes for any of this.
- **Quicker, not different.** The other change only makes touches quicker: upstream looks every point of a touch up
  against the app on screen, an accessibility round trip of about 240 ms a point, and SimMirror uses a point where it
  is given, since an iPhone's app fills the screen. A tap took 0.68 s before and 0.34 s after on an iPhone 14 Pro. A
  checkout of your own need not carry it; a change to either is built again by itself.
- **Read-only by default.** Listing, the lock state, the screen and the log are reads. SimMirror reuses the pairing the
  Mac already has; it never pairs, unpairs, changes trust or touches the passcode.
- **Every change is put back.** What SimMirror changes -- light or dark, text size, contrast, reduce motion, the status
  bar, a simulated location -- is read first and put back when the device is let go (`device.restore_changes`, on for
  real devices by default). It is also written down, private to you (`device-changes.json` in the run folder, 0600),
  so if the daemon crashes or is killed, the next start puts it back; what cannot be put back then, with the device
  unplugged, is kept for the start after, or for the next time the device is let go once it is back.
- **What an agent sees goes to its model.** Screenshots, snapshots and logs an agent reads are sent by its client to
  the agent's model provider, as for a simulator. On your own phone, a Focus mode and closing private apps first keep
  notifications and messages out of what it sees.
- **A person decides.** WebDriverAgent is installed only once a person sets it up; agents switch to a real device only
  while `real_devices.agents_choose` is on; and a page can turn on real devices, WebDriverAgent, agents choosing or a
  signing team only with a code from the terminal (these settings are [sensitive](settings.md#sensitive-settings)).
- **What is kept is private.** Logs stay in the daemon's memory; recordings are files only you can read
  ([Recording](recording.md#privacy)).

## Limits

- A locked device shows its lock screen; nothing types its passcode.
- A device on its side is shown upright in portrait; WebDriverAgent says only "landscape", so touches on a device held
  the other way round may land mirrored.
- Screens that protect their content -- video with DRM, some banking apps -- may show black.
- A touch starts once the finger lifts, and a drag is replayed at its own speed. The historical tap
  measured about 0.34 s; this is not the latency of a complete `sim_act` call with screen checks and waits.
- A screen WebDriverAgent cannot read within 5 seconds (`real_devices.wda.source_timeout_ms`) -- a busy web page in
  Safari, a table of hundreds of rows -- is read from its pixels instead. See [long screens](#long-screens).
- Logs, the cable screen and WebDriverAgent need the cable; over Wi-Fi a device has tier 1 without its log.
- A device Xcode also reaches over the network is still connected with its cable out, and SimMirror keeps it at tier
  1 over Wi-Fi. It is refused as not connected only once it cannot be reached either way. While it is, SimMirror
  listens to usbmuxd for the cable, and as it goes back in attaches a second session beside the first -- the cable
  screen and WebDriverAgent with it -- which takes the first one's place once it is up. A device not yet reachable
  the moment its cable is back keeps the session it had, and is tried again; `sim_device restart` starts it over at
  once.

### Long screens

WebDriverAgent serves one request at a time: every route runs on its main queue. A tree SimMirror stops waiting for
is still built to its end, and everything sent after it -- the next tree, a tap, `/status`, a screenshot -- waits
behind it: a snapshot that asked for the tree again would time out in turn, and so would every one after it. So:

- **The tree is asked for less.** WebDriverAgent leaves out of each element what SimMirror never reads (its
  `excluded_attributes`: `accessible`, `nativeAccessibilityElement`, `focused`, `traits`, `frame`, `nativeFrame`,
  `placeholderValue`, `minValue`, `maxValue`), and reads at most `real_devices.wda.source_max_children` children of
  each element (200), 50 levels deep. A table lists every row, those scrolled off screen too, so a long one is read
  only to its 200th; a snapshot says so when a list may have been cut there.
- **A tree not built in time is not asked for again until it is done.** The snapshot reads the screen's pixels and
  says why. The next snapshots ask WebDriverAgent's `/status` first, waiting a second at most: it answers only once
  the tree ahead of it is built, and until then they read the pixels at once, saying the element tree is still being
  built on the device. Then the tree is read again.
- **The pixels do not wait behind the tree.** With the cable screen they are read from it. Where WebDriverAgent
  takes the screenshots, a busy one is passed over for devicectl's.
- **Nor does a touch.** A tap, a swipe, typing or a button waits up to 5 seconds for WebDriverAgent to finish the
  tree. Still busy then, it is refused unsent, saying so, rather than land whenever the tree is done -- up to half a
  minute later, on a screen that may have moved since.
- **A long answer is parsed off the event loop**, on a thread: a 6 MB tree parses in about 25 ms.
- **When the pixels cannot be read either**, the error says both causes.

## Checked on a phone

`make live-device` ([contributing](../CONTRIBUTING.md#setup)) runs a real device's lifecycle on a cabled phone. Run
on an iPhone 17 Pro (iPhone18,1), iOS 27.0 (24A437), Xcode 26.6 (17F113), on 2026-10-16 -- view only, and driven by
WebDriverAgent 16.12.10 signed by a development team:

| | View only | WebDriverAgent |
|---|---|---|
| `restart` reconnects, says the phone was not restarted, and a shutdown is refused | passed | passed |
| Unplugged and off the network, `restart` and `boot` are refused saying why, no simulator is started; plugged back in, it comes back | passed | passed |
| Turned light by a runtime that then crashed, the next start puts it back dark | passed | passed |
| `sim_act` offered only with WebDriverAgent | passed | passed |
| Cable out while Wi-Fi reaches it: reached without it, its note said; plugged back in, attached again by itself, `sim_device health` ready | -- | passed (2026-10-26, with the cable screen) |
| Reboot, restart, shutdown, erase, uninstall, unpair or delete asked of the phone | none | none |

## Settings

The settings panel's **Real devices** tab: `real_devices.enabled`, `agents_choose`, `developer_dir`, `screen` (`auto`,
`usb`, `wda`, `screenshot`), `capture_timeout`, `log_buffer_mb`, `team_id`, and `wda.enabled`, `wda.path`,
`wda.startup_timeout`, `wda.keep_running`, `wda.source_timeout_ms` and `wda.source_max_children`. The Device tab's `device.restore_changes` (`real_devices`, `all` or `off`) says whose changes
are put back. See the [configuration reference](reference/configuration.md).

## Troubleshooting

| It says | Do |
|---|---|
| `… is not connected: plug it in and unlock it` | Connect the device, unlock it, and choose Trust if it asks |
| `… cannot touch or read this device now. … WebDriverAgent is reached only over the device's cable` | Plug the device straight into the Mac (not through a hub) and unlock it: it is attached again by itself within seconds, or at once with `sim_device restart`. `sim-mirror wda status` says how touch stands on the device now |
| `WebDriverAgent was started on the device but does not answer now` | Unlock the device and check its cable; `sim_device restart` starts WebDriverAgent over |
| `WebDriverAgent did not build the element tree within 5 s` | The screen was read from its pixels, and is until WebDriverAgent is done ([long screens](#long-screens)). Raise `real_devices.wda.source_timeout_ms` to wait longer, or lower `real_devices.wda.source_max_children` for a shorter tree |
| `WebDriverAgent is still building an element tree an earlier read gave up on; the touch was not sent` | Nothing was done on the device. Call again in a moment, or wait for a snapshot that reads the tree again ([long screens](#long-screens)) |
| `WebDriverAgent closed the connection without answering; it may have restarted` | A read was asked once more already. If it keeps happening, `sim_device health` says whether WebDriverAgent still runs |
| `the text reader did not answer a reading of the screen within 5 seconds` | It is started again for the next reading. If it keeps happening, look in `daemon.log` for `the event loop was held` or `reading the screen took`, which say whether the daemon or Vision was slow; `perception.ocr_timeout_ms` gives a reading longer |
| `Its cable could not show the screen (… Camera …)` | Allow sim-mirror-helper in System Settings › Privacy & Security › Camera, then pick the device again |
| `no screen of a cabled device called … is among this Mac's capture devices` | Unplug and plug the cable back in with the device unlocked |
| `Its profile does not cover this device` | `sim-mirror wda setup --device <udid>` |
| `Your development team has reached the maximum number of registered iPhone devices` | A personal team registers only a few devices: remove one in your developer account, or use a team that has room |
| `WebDriverAgent ended as it started` | On the device, trust your developer and turn on Enable UI Automation; the message names its log |
| `WebDriverAgent is signed by a development team, and none is known here` | Set `real_devices.team_id`: `sim-mirror wda teams` lists the teams this Mac signs for |
| `WebDriverAgent could not be set up` | Sign in to Xcode › Settings › Accounts if it says the account was refused, then press **Try again** |

`sim-mirror doctor` checks all of this: [the doctor](doctor.md).
