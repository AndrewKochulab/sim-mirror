# Connectors

A connector is how SimMirror reaches a device: it says what it can do there, and hands over the parts to drive it.
Booting, installing and launching are simctl's whichever connector is in use; a connector owns the screen, input and
the element tree.

## Capabilities

| Capability | Means |
|---|---|
| `lifecycle` | Boot, shut down and restart devices |
| `device_list` | List this Mac's simulators |
| `appearance` | Switch light and dark |
| `open_url` | Open a URL on the device |
| `app_install`, `app_launch` | Install a built app; launch and terminate apps |
| `logs` | Read the device's log |
| `screenshot` | A JPEG of the screen |
| `stream_jpeg`, `stream_h264` | The live screen as JPEG frames or as H.264 |
| `input_touch`, `input_button`, `input_key`, `input_text` | Touches and gestures, hardware buttons, keys, text |
| `element_tree` | What is on screen, as an accessibility tree |
| `build_preview` | Reserved for build integrations |

The viewer offers only the controls the connector can serve, and an agent is offered only the tools it can: without
`input_touch` there is no `sim_act`, without `element_tree` no `sim_snapshot`. See the
[tool reference](reference/tools.md) for what each tool needs.

## Built in

| Connector | Needs | Can do |
|---|---|---|
| `native` | Only Xcode: SimMirror's own helper comes in the package, or `sim-mirror helper build` builds it | Everything above except `build_preview`: JPEG and H.264, all input, the element tree |
| `idb` | idb_companion 1.5, from Homebrew or `connectors.idb.companion_path` | Everything above except `build_preview`: JPEG and H.264, all input, the element tree |
| `simctl` | Only Xcode | View-only: JPEG at up to 4 frames a second, lifecycle, device list, appearance, open URL, install, launch, logs, screenshots; its screen is [read from its pixels](screen-understanding.md#read-from-pixels) while `perception.ocr` is on |
| `mcpbridge` | Xcode 27, and Xcode's approval | Everything simctl can, and the element tree, read through Xcode's UI hierarchy; no input |

![The viewer on the simctl connector: a View only badge, and the buttons that need touch gone](media/view-only.png)

### native

SimMirror's own helper, `sim-mirror-helper`, drives a device without idb_companion. It is a small Swift program that
reaches the simulator through the Mac's CoreSimulator and Xcode's SimulatorKit, as Simulator.app does: it reads the
device's screen straight from its framebuffer, encodes H.264 with VideoToolbox's low-latency encoder only when the
screen changes, sends touches, buttons and keys through the simulator's own input service, and reads the element tree
through the accessibility translator. One helper serves each device on a unix socket in the run folder, in a process
group of its own; it ends when the device is let go, when the SimMirror that started it goes, and when the device
shuts down.

- **Which helper.** `connectors.native.helper_path` when set; else the one in the package (the wheel on PyPI carries
  one built for both Mac architectures); else the one `sim-mirror helper build` built with the Xcode in use. A helper
  from another SimMirror version is not used. `sim-mirror helper status` says which is used and why.
- **Input.** `connectors.native.hid_transport`: `auto` sends through dtuhid, the input service simulators run with
  CoreSimulator 1155.4 or later, and through SimulatorKit's older Indigo messages before that.
- **Starting.** The helper opens the device's screen and warms its encoders before it answers, so the first frame
  comes at once; `connectors.native.startup_timeout` bounds that. A still screen sends a key frame every second
  (`connectors.native.idle_key_frames`), so a viewer that joins sees it without waiting for a change.

What it measured against idb and simctl, with `benchmarks/connector_latency.py` (10 rounds each, 2026-09-17, macOS
26.6.2, an Apple Silicon Mac) -- iOS 26.5 on Xcode 26.6:

| Connector | Attach ms | First H.264 frame ms | Screenshot 900 px p50 / p95 ms | Screenshot 160 px p50 / p95 ms | Snapshot p50 / p95 ms (elements) | Input p50 / p95 ms | Tap to change p50 / p95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| native | 692.0 | 34.9 | 7.4 / 8.4 | 2.3 / 3.0 | 43.3 / 97.1 (16) | 0.3 / 3.8 | 624.7 / 690.9 |
| idb | 623.5 | 376.8 | 10.3 / 52.4 | 2.7 / 3.9 | 56.7 / 126.8 (15) | 0.3 / 548.6 | 644.6 / 671.2 |
| simctl | 528.6 | – | 590.6 / 607.0 | 566.0 / 585.0 | – | – | – |

and iOS 27.0 on Xcode 27.0:

| Connector | Attach ms | First H.264 frame ms | Screenshot 900 px p50 / p95 ms | Screenshot 160 px p50 / p95 ms | Snapshot p50 / p95 ms (elements) | Input p50 / p95 ms | Tap to change p50 / p95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| native | 474.6 | 35.9 | 6.8 / 8.3 | 2.5 / 2.9 | 42.0 / 92.0 (16) | 0.3 / 9.8 | 304.2 / 418.1 |
| idb | 412.0 | 351.8 | 8.4 / 39.2 | 1.9 / 3.4 | 59.2 / 77.6 (16) | 1.0 / 558.3 | 327.6 / 514.1 |
| simctl | 407.1 | – | 489.8 / 520.4 | 448.5 / 474.1 | – | – | – |

Attach is starting the connector until it describes the screen; the native helper spends that warming its encoders,
which is why its first H.264 frame follows in tens of milliseconds, where idb's takes hundreds. Input is a tap's events
until the connector says they went out; tap to change is a tap on Settings' General row until the screen differs, and
is mostly iOS drawing the next page. Snapshots count the elements each read. Per device, then: the first frame
comes 250 to 300 ms sooner from attaching, though attaching itself takes 60 to 70 ms longer; screenshots and snapshots
are as fast or faster -- but for a 160-pixel screenshot's median and a snapshot's p95 on iOS 27.0 -- and a large
screenshot's p95 a fifth of idb's or less; and a tap's events go out without idb's occasional half-second stall.

### idb

The idb connector starts one idb_companion per booted device, in a process group of its own, serving on a unix socket
in the run folder -- no TCP port -- and ends it when the device is let go. A companion a crashed SimMirror left behind is
ended the next time it starts, and never one another host started.

### mcpbridge

Xcode 27 ships `mcpbridge`, which lets an agent use Xcode's tools. The mcpbridge connector shows the screen as simctl
does and reads it through one of those tools: a capture of the device's UI hierarchy. So an agent can take snapshots on
a Mac without idb_companion. What was measured on Xcode 27.0 with an iOS 27.0 simulator (2026-09-16):

- **It needs no window.** mcpbridge reaches Xcode's tool service, which runs without a window and starts when it is not
  running; Xcode itself is never opened. It follows `device.developer_dir`, so it works while `xcode-select` names an
  older Xcode.
- **Xcode approves it first.** Xcode lets an agent use its tools once the agent has opened a project through them,
  and asks you then if it is set to ask. Run `sim-mirror xcode approve` in a project's folder, or give it a
  `.xcodeproj` or `.xcworkspace`: it opens that project through Xcode's tools and closes it again. The approval is
  for the program that runs mcpbridge -- SimMirror's own Python -- and outlasts Xcode restarting.
- **A read takes 0.2 to 0.9 seconds** once a session is open, and opening one 0.05 to 1.8. A simulator can be in one
  session at a time, so SimMirror ends its session a minute after the last read and when the device is let go; an
  agent working in Xcode can use the simulator in between. A session SimMirror left behind is taken back; another
  agent's is left alone, and SimMirror says whose it is.
- **It does not touch.** A tap through Xcode's tools answered after 3.3 seconds and a button press after 5.7, since
  each waits for the screen to settle and captures it -- too slow for a person's hand or an agent's steps -- so it
  offers no input, and agents are not offered `sim_act`.

Xcode 26.6 also has an `mcpbridge`, but it reaches only an Xcode that is open, and was not measured; SimMirror counts
only Xcode 27 and later.

## Choosing one

`connectors.preferred`:

- **`auto`** (the default) tries `native`, then `idb`, then `simctl`, fastest first, and uses the first that can be
  used here. A connector that can be used here but cannot reach a device -- the helper does not start in time, or the
  device's input does not answer -- gives way to the next one that can do as much, for that device. When it falls back
  it says why -- in the viewer, in `sim-mirror doctor`, and in the refusal of a tool that needs more -- so a Mac with
  neither still shows the screen and says what to do to touch it.
- **A connector's name** uses that one, or refuses with its reasons. It never quietly uses another. `mcpbridge` is
  used only this way.

Changing it takes effect at once: a running device is brought back on the new connector.

## Merging Xcode's hierarchy

idb_companion's accessibility tree leaves out a web page's text and links, a widget's text and the status bar; Xcode
27's UI hierarchy has them. With **`connectors.mcpbridge.merge`** on, a device idb drives is read both ways for every
snapshot: idb's tree whole, and from Xcode's only what idb does not already say in the same place, whatever each calls
it. The added elements get refs like any other, and a tap on one goes through idb.

Measured on iOS 27.0 (2026-09-16): Safari showing example.com went from 5 elements to 8 -- its heading, its text and
its **Learn more** link, which an agent then tapped -- and the home screen from 13 to 19, with a widget's text, the
page indicator, the time and Wi-Fi. Settings, a SwiftUI form, an alert and the keyboard came out the same as idb alone.
Each snapshot takes 0.2 to 0.9 seconds longer, so it is off by default. When Xcode cannot read the screen -- not
approved, another agent's session -- the snapshot still comes from idb, and says why.

## Merging an app's own hierarchy

An app under development that links [SimMirrorKit](app-sdk.md) shares its own UIKit and SwiftUI views. With
**`connectors.app.merge`** on -- the default, which changes nothing while no app shares -- every snapshot of a device
also asks the app in front for them, whichever connector drives it, and merges them the same way: the connector's tree
whole, adding what it does not say in the same place. With **`connectors.app.name_unlabeled`** on, it also names what
the connector found without a label, and what the app's developer named with `.simMirror`, keeping each element where
it was. Taps on an added element go through the connector like any other. The app's views come first, then Xcode's,
then [a screen's pixels](screen-understanding.md#read-from-pixels).

Measured on the sample app (iPhone 17 Pro, iOS 26.5, idb, 2026-09-17): its UIKit form gained icon segments, a stepper,
a hand-drawn rating control and a card with a tap gesture, its fields were named, and its tabs, which idb left out, were
added; asking the app took 1 to 30 ms. An app that does not answer within `connectors.app.timeout_ms` -- paused in the
debugger, busy -- is skipped, and the snapshot says so while it is in front.

## More connectors

A package registers a connector under the `sim_mirror.connectors` entry point, and SimMirror finds it when it starts;
`sim-mirror version` and `sim-mirror doctor` list it. Choose it by name in `connectors.preferred`.

Planned: real iPhones
(first without signing, then WebDriverAgent), and Android emulators. Write your own with the
[connector guide](contributing/connector-guide.md).
