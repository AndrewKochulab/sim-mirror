# Real devices

An iPhone or iPad connected to your Mac shows up in SimMirror beside the simulators: in the viewer's device picker, in
`sim-mirror devices`, and to an agent once you pick it. What SimMirror can do with it depends on what is there:

| | Needs | Gives |
|---|---|---|
| **Tier 1** | Xcode, and the device trusted and in Developer Mode | The device listed and picked; a screenshot about once a second; apps installed, launched and ended; its look and place changed; its log, over the cable |
| **The cable screen** | The device plugged in by cable, and the Camera permission for SimMirror's helper once | The live screen as H.264, like a simulator's |
| **Tier 2** | WebDriverAgent, set up once with your signing team (**Set up touch** in the viewer) | Touching, typing, buttons, and the element tree for `sim_snapshot` |

Without tier 2 the device is view-only: an agent has `sim_screenshot`, `sim_app`, `sim_device`, `sim_record` and a
`sim_snapshot` read from the screen's pixels, and no `sim_act`.

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
the whole thing off; a host that embeds SimMirror has it off unless it turns it on.

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
  record, never a new pairing -- and kept while the device is driven, up to `real_devices.log_buffer_mb`.

## The cable screen

With the device plugged in, SimMirror's native helper reads its screen the way QuickTime does and serves it as a
simulator's: screenshots, and H.264 at the viewer's frame rate. The first time, macOS asks whether
**sim-mirror-helper** may use the Camera -- that is how macOS names reading a device's screen -- and SimMirror uses
screenshots until you allow it. While the screen is read, the device's status bar shows 9:41; the capture runs only
while someone looks and for 30 seconds after, then the device's own status bar comes back.

Measured on an iPhone 14 Pro, iOS 26.3, over USB (2026-09-29):

| | |
|---|---|
| First picture after plugging in | about 7.5 s (the cable switches mode), then about 1 s |
| A screenshot | 6 to 24 ms |
| First H.264 frame of a stream | 28 ms |
| Frames a second at 30 asked | about 28 |
| Any agent call | about 60 ms |

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
   `27343e6064b204f7a1bff20096597d5f318564465d91e2fee66ea71bc1ddc222`, makes [one change](#safety), and builds it with
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
has it off unless it turns it on. The runner ends when the device is let go unless `real_devices.wda.keep_running` says
otherwise. `sim-mirror wda status` says what is set up; `sim-mirror wda uninstall <udid>` removes the runner from a
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

A finger's stroke goes as one request once it lifts, so a tap lands about half a second after it starts; text of any
kind -- accents, emoji, other scripts -- is typed whole, never through the pasteboard.

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
- **Read-only by default.** Listing, the lock state, the screen and the log are reads. SimMirror reuses the pairing the
  Mac already has; it never pairs, unpairs, changes trust or touches the passcode.
- **Every change is put back.** What SimMirror changes -- light or dark, text size, contrast, reduce motion, the status
  bar, a simulated location -- is read first and put back when the device is let go (`device.restore_changes`, on for
  real devices by default). It is also written down, private to you (`device-changes.json` in the run folder, 0600),
  so if the daemon crashes or is killed, the next start puts it back; what cannot be put back then, with the device
  unplugged, is kept for the start after.
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
- A WebDriverAgent tap lands about 0.5 s after it starts.
- Logs, the cable screen and WebDriverAgent need the cable; over Wi-Fi a device has tier 1 without its log.

## Settings

The settings panel's **Real devices** tab: `real_devices.enabled`, `agents_choose`, `developer_dir`, `screen` (`auto`,
`usb`, `wda`, `screenshot`), `capture_timeout`, `log_buffer_mb`, `team_id`, and `wda.enabled`, `wda.path`,
`wda.startup_timeout` and `wda.keep_running`. The Device tab's `device.restore_changes` (`real_devices`, `all` or `off`) says whose changes
are put back. See the [configuration reference](reference/configuration.md).

## Troubleshooting

| It says | Do |
|---|---|
| `… is not connected: plug it in and unlock it` | Connect the device, unlock it, and choose Trust if it asks |
| `Its cable could not show the screen (… Camera …)` | Allow sim-mirror-helper in System Settings › Privacy & Security › Camera, then pick the device again |
| `no screen of a cabled device called … is among this Mac's capture devices` | Unplug and plug the cable back in with the device unlocked |
| `Its profile does not cover this device` | `sim-mirror wda setup --device <udid>` |
| `Your development team has reached the maximum number of registered iPhone devices` | A personal team registers only a few devices: remove one in your developer account, or use a team that has room |
| `WebDriverAgent ended as it started` | On the device, trust your developer and turn on Enable UI Automation; the message names its log |
| `WebDriverAgent is signed by a development team, and none is known here` | Set `real_devices.team_id`: `sim-mirror wda teams` lists the teams this Mac signs for |
| `WebDriverAgent could not be set up` | Sign in to Xcode › Settings › Accounts if it says the account was refused, then press **Try again** |

`sim-mirror doctor` checks all of this: [the doctor](doctor.md).
